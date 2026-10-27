"""Narrow, read-only Skyrim SE ESS reader for the verified Skyshards ledger.

Offsets in the file-location table refer to the virtual uncompressed file. This
module intentionally reads no Papyrus VM or arbitrary ChangeForm payloads.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import lz4.block

from .models import Catalog, PlayerLocation
from .form_ids import PluginKind, RuntimeFormIds, from_save_tables


MAX_FILE = 256 * 1024 * 1024
MAX_BODY = 256 * 1024 * 1024
MAX_SCREENSHOT = 32 * 1024 * 1024
MAX_FORM_IDS = 2_000_000
MAX_CHANGE_FORMS = 2_000_000
MAX_TABLE_BLOCKS = 100_000


class SaveFormatError(ValueError):
    """An ESS is truncated, corrupt, or structurally inconsistent."""


class SaveAccessError(SaveFormatError):
    """The ESS could not be opened; a later poll may succeed unchanged."""


class UnsupportedSaveError(SaveFormatError):
    """The ESS uses a version or compression mode this reader cannot parse."""


@dataclass(frozen=True, slots=True)
class ParsedSave:
    path: Path
    character: str
    save_number: int
    header_version: int
    form_version: int
    skyshards_index: int | None
    resolved_shard_count: int
    absorbed_members: tuple[int, ...] | None
    absorbed_list_error: str | None
    counters: Mapping[str, float | None]
    warnings: tuple[str, ...]
    player_location: PlayerLocation
    skyshards_kind: PluginKind = "light"

    @property
    def runtime_forms(self) -> RuntimeFormIds | None:
        return (RuntimeFormIds(self.skyshards_kind, self.skyshards_index)
                if self.skyshards_index is not None else None)

    @property
    def skyshards_light_index(self) -> int | None:
        return self.skyshards_index if self.skyshards_kind == "light" else None


def _require(data: bytes | memoryview, pos: int, size: int, label: str) -> None:
    if pos < 0 or size < 0 or pos + size > len(data):
        raise SaveFormatError(f"{label}: truncated or out of bounds at {pos}, length {size}")


def _u8(data: bytes | memoryview, pos: int, label: str) -> int:
    _require(data, pos, 1, label)
    return data[pos]


def _u16(data: bytes | memoryview, pos: int, label: str) -> int:
    _require(data, pos, 2, label)
    return struct.unpack_from("<H", data, pos)[0]


def _u32(data: bytes | memoryview, pos: int, label: str) -> int:
    _require(data, pos, 4, label)
    return struct.unpack_from("<I", data, pos)[0]


def _wstring(data: bytes | memoryview, pos: int, label: str) -> tuple[str, int]:
    length = _u16(data, pos, label)
    pos += 2
    _require(data, pos, length, label)
    try:
        return bytes(data[pos:pos + length]).decode("utf-8"), pos + length
    except UnicodeDecodeError as exc:
        raise SaveFormatError(f"{label}: invalid UTF-8") from exc


def _vsval(data: bytes | memoryview, pos: int) -> tuple[int, int]:
    first = _u8(data, pos, "VSVal")
    size = (1, 2, 3, 3)[first & 3]
    _require(data, pos, size, "VSVal")
    return int.from_bytes(data[pos:pos + size], "little") >> 2, pos + size


def _refid(data: bytes | memoryview, pos: int, form_ids: tuple[int, ...]) -> tuple[int | None, int]:
    _require(data, pos, 3, "RefID")
    raw = int.from_bytes(data[pos:pos + 3], "big")
    kind, value = raw >> 22, raw & 0x3FFFFF
    if kind == 0:
        return (form_ids[value - 1] if 0 < value <= len(form_ids) else None), pos + 3
    if kind == 1:
        return value, pos + 3
    if kind == 2:
        return 0xFF000000 | value, pos + 3
    return None, pos + 3


def _inflated(payload: bytes | memoryview, expected: int, label: str) -> bytes:
    if expected > MAX_BODY:
        raise SaveFormatError(f"{label}: unreasonable uncompressed size {expected}")
    try:
        decompressor = zlib.decompressobj()
        result = decompressor.decompress(payload, expected + 1)
    except zlib.error as exc:
        raise SaveFormatError(f"{label}: invalid zlib payload") from exc
    if len(result) != expected or not decompressor.eof or decompressor.unused_data:
        raise SaveFormatError(f"{label}: uncompressed size mismatch")
    return result


def _read_header(data: bytes) -> tuple[str, int, int, int, int, str]:
    if len(data) < 25 or data[:13] != b"TESV_SAVEGAME":
        raise SaveFormatError("invalid TESV_SAVEGAME signature or truncated header")
    header_size, version, save_number = struct.unpack_from("<III", data, 13)
    if version != 12:
        raise UnsupportedSaveError(f"unsupported ESS header version {version}; expected 12")
    if header_size < 70 or header_size > 1_000_000 or 17 + header_size > len(data):
        raise SaveFormatError("invalid ESS header size")
    pos = 25
    player, pos = _wstring(data, pos, "character name")
    _require(data, pos, 4, "level")
    pos += 4
    location, pos = _wstring(data, pos, "location")
    for label in ("game date", "race"):
        _, pos = _wstring(data, pos, label)
    _require(data, pos, 18 + 10, "header fields")
    pos += 18  # sex, XP, next-level XP, file time
    width, height, compression = struct.unpack_from("<IIH", data, pos)
    pos += 10
    if pos != 17 + header_size:
        raise SaveFormatError("ESS header field lengths disagree with header size")
    screenshot = width * height * 4
    if width == 0 or height == 0 or screenshot > MAX_SCREENSHOT:
        raise SaveFormatError("invalid screenshot dimensions")
    _require(data, pos, screenshot, "screenshot")
    pos += screenshot
    if compression != 2:
        raise UnsupportedSaveError(f"unsupported ESS compression {compression}; expected LZ4 (2)")
    return player, save_number, version, pos, compression, location


def unavailable_player_location(reason: str, saved_location: str | None = None,
                                current_cell_form_id: str | None = None) -> PlayerLocation:
    return PlayerLocation(status="unavailable", reason=reason, saved_location=saved_location,
                          current_cell_form_id=current_cell_form_id, worldspace=None,
                          is_exterior=None, x=None, y=None, z=None, rotation=None, evidence=())


def _decode_player_location(payload: bytes | memoryview, flags: int, form_type: int,
                            form_ids: tuple[int, ...], header_location: str,
                            skyrim_master_valid: bool) -> PlayerLocation:
    """Read only ACHR initial data; the first RefID is the *current* cell or WRLD.

    FallrimTools ChangeFormACHR/ChangeFormInitialData type 4/6 layouts provide
    cell RefID followed by three position and three rotation float32 values.
    """
    if form_type != 1:
        return unavailable_player_location("Player ChangeForm is not ACHR", header_location)
    if not flags & ((1 << 1) | (1 << 2) | (1 << 3) | (1 << 25)):
        return unavailable_player_location("Player ACHR has no current cell/position fields", header_location)
    required = 34 if flags & ((1 << 3) | (1 << 25)) else 27
    if len(payload) < required:
        return unavailable_player_location("Player ACHR position fields are truncated", header_location)
    cell_id, _ = _refid(payload, 0, form_ids)
    if cell_id is None:
        return unavailable_player_location("Player current cell/worldspace RefID cannot be resolved", header_location)
    cell_hex = f"{cell_id:08X}"
    values = struct.unpack_from("<6f", payload, 3)
    if not all(math.isfinite(value) for value in values):
        return unavailable_player_location("Player ACHR position or rotation is non-finite", header_location,
                                           cell_hex)
    if cell_id != 0x0000003C:
        return unavailable_player_location("Player current cell/worldspace is not Tamriel; it may be an interior or another worldspace",
                                           header_location, cell_hex)
    if not skyrim_master_valid:
        return unavailable_player_location("Skyrim.esm identity could not be verified", header_location, cell_hex)
    if header_location != "Skyrim":
        return unavailable_player_location("Save header location disagrees with Tamriel player reference",
                                           header_location, cell_hex)
    return PlayerLocation(status="available", reason=None, saved_location=header_location,
                          current_cell_form_id=cell_hex, worldspace="Tamriel", is_exterior=True,
                          x=values[0], y=values[1], z=values[2], rotation=values[5],
                          evidence=("Player ACHR current cell/WRLD RefID 0000003C",
                                    "Player ACHR initial position and rotation", "save header location Skyrim"))


def _body(data: bytes, pos: int) -> bytes:
    _require(data, pos, 8, "compression metadata")
    unpacked, packed = struct.unpack_from("<II", data, pos)
    pos += 8
    if unpacked == 0 or unpacked > MAX_BODY or packed == 0 or packed > MAX_FILE:
        raise SaveFormatError("invalid compressed or uncompressed size")
    if pos + packed != len(data):
        raise SaveFormatError("compressed body size disagrees with ESS file length")
    try:
        result = lz4.block.decompress(data[pos:pos + packed], uncompressed_size=unpacked)
    except (ValueError, lz4.block.LZ4BlockError) as exc:
        raise SaveFormatError("invalid LZ4 compressed body") from exc
    if len(result) != unpacked:
        raise SaveFormatError("LZ4 uncompressed size mismatch")
    return result


def parse_save(path: Path, catalog: Catalog) -> ParsedSave:
    """Parse only plugin identities, FormID array, globals, and absorbed FormList."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE + 1)
    except OSError as exc:
        raise SaveAccessError(f"cannot read ESS: {exc}") from exc
    if len(data) > MAX_FILE:
        raise SaveFormatError("ESS exceeds 256 MiB safety limit")
    character, save_number, header_version, virtual_header_size, _, header_location = _read_header(data)
    body = _body(data, virtual_header_size)
    form_version = _u8(body, 0, "form version")
    if form_version != 78:
        raise UnsupportedSaveError(f"unsupported ESS form version {form_version}; expected 78")
    info_size = _u32(body, 1, "plugin-info size")
    pos = 5
    full_count = _u8(body, pos, "full plugin count")
    pos += 1
    full_names = []
    for _ in range(full_count):
        name, pos = _wstring(body, pos, "full plugin")
        full_names.append(name)
    lite_count = _u16(body, pos, "light plugin count")
    pos += 2
    if lite_count > 4096:
        raise SaveFormatError("light plugin count exceeds ESL index range")
    light_names = []
    for _ in range(lite_count):
        name, pos = _wstring(body, pos, "light plugin")
        light_names.append(name)
    if pos != info_size + 5:
        raise SaveFormatError("plugin-info size mismatch")
    if len(set(name.lower() for name in full_names + light_names)) != len(full_names) + len(light_names):
        raise SaveFormatError("duplicate plugin names in ESS load order")
    resolver, plugin_warning = from_save_tables(catalog.plugin_kind, full_names, light_names)
    _require(body, pos, 100, "file location table")
    locations = struct.unpack_from("<25I", body, pos)
    raw_offsets = locations[:6]
    form_array_off, unknown_off, table1_off, table2_off, changes_off, table3_off = (
        value - virtual_header_size for value in raw_offsets)
    if not (pos + 100 <= table1_off <= table2_off <= changes_off <= table3_off <= form_array_off <= unknown_off <= len(body)):
        raise SaveFormatError("file location table offsets are out of order or out of bounds")
    table1_count, table2_count, _, change_count = locations[6:10]
    if table1_count > MAX_TABLE_BLOCKS or table2_count > MAX_TABLE_BLOCKS or change_count > MAX_CHANGE_FORMS:
        raise SaveFormatError("unreasonable table or ChangeForm count")
    form_id_count = _u32(body, form_array_off, "FormID count")
    if form_id_count == 0 or form_id_count > MAX_FORM_IDS:
        raise SaveFormatError("unreasonable FormID array count")
    _require(body, form_array_off + 4, 4 * form_id_count, "FormID array")
    if form_array_off + 4 + 4 * form_id_count > unknown_off:
        raise SaveFormatError("FormID array overlaps next section")
    form_ids = struct.unpack_from(f"<{form_id_count}I", body, form_array_off + 4)
    shard_ids = {resolver.resolve(local) for local in catalog.by_local_id} if resolver else set()
    resolved_shard_count = len(shard_ids.intersection(form_ids))
    wanted_globals = {resolver.resolve(local): name for name, local in catalog.counter_local_ids.items()} if resolver else {}
    counters: dict[str, float | None] = {name: None for name in catalog.counter_local_ids}
    warnings: list[str] = []

    for start, end, count, table_name in ((table1_off, table2_off, table1_count, "table 1"),
                                          (table2_off, changes_off, table2_count, "table 2")):
        cursor = start
        for _ in range(count):
            _require(body, cursor, 8, table_name)
            block_type, length = struct.unpack_from("<II", body, cursor)
            cursor += 8
            if cursor + length > end:
                raise SaveFormatError(f"{table_name}: block exceeds table boundary")
            if block_type == 3:
                block = memoryview(body)[cursor:cursor + length]
                entries, at = _vsval(block, 0)
                if entries > 2_000_000 or entries > (len(block) - at) // 7:
                    raise SaveFormatError(f"{table_name}: unreasonable global count")
                for _ in range(entries):
                    form_id, at = _refid(block, at, form_ids)
                    _require(block, at, 4, "global value")
                    value = struct.unpack_from("<f", block, at)[0]
                    at += 4
                    if form_id in wanted_globals:
                        name = wanted_globals[form_id]
                        if counters[name] is not None:
                            warnings.append(f"duplicate global {name}")
                        elif not math.isfinite(value):
                            warnings.append(f"non-finite global {name}")
                        else:
                            counters[name] = value
                if at != len(block):
                    raise SaveFormatError(f"{table_name}: global block length mismatch")
            cursor += length
        if cursor != end:
            raise SaveFormatError(f"{table_name}: length/count mismatch")

    absorbed_form = resolver.resolve(catalog.absorbed_list_local_id) if resolver else None
    absorbed_members: tuple[int, ...] | None = None
    absorbed_error: str | None = None
    player_location = unavailable_player_location("Player ACHR ChangeForm missing", header_location)
    player_found = 0
    cursor = changes_off
    found = 0
    for _ in range(change_count):
        form_id, cursor = _refid(body, cursor, form_ids)
        _require(body, cursor, 6, "ChangeForm header")
        flags, type_field, _version_byte = struct.unpack_from("<IBB", body, cursor)
        cursor += 6
        width_index = type_field >> 6
        if width_index > 2:
            raise SaveFormatError("invalid ChangeForm length width")
        width = (1, 2, 4)[width_index]
        _require(body, cursor, width * 2, "ChangeForm lengths")
        length = int.from_bytes(body[cursor:cursor + width], "little")
        expanded = int.from_bytes(body[cursor + width:cursor + width * 2], "little")
        cursor += width * 2
        if cursor + length > table3_off:
            raise SaveFormatError("ChangeForm payload exceeds section")
        if form_id == 0x00000014:
            player_found += 1
            if player_found > 1:
                player_location = unavailable_player_location("Duplicate player ACHR ChangeForms", header_location)
            else:
                payload = memoryview(body)[cursor:cursor + length]
                if expanded:
                    try:
                        payload = _inflated(payload, expanded, "player ACHR")
                    except SaveFormatError:
                        player_location = unavailable_player_location("Player ACHR compression is invalid", header_location)
                        payload = None
                if payload is not None:
                    player_location = _decode_player_location(payload, flags, type_field & 0x3F,
                                                              form_ids, header_location,
                                                              bool(full_names and full_names[0].lower() == "skyrim.esm"))
        if form_id == absorbed_form:
            found += 1
            if found > 1:
                absorbed_error = "duplicate absorbed FormList ChangeForms"
            elif type_field & 0x3F != 43 or not flags & 0x80000000 or flags & 2:
                absorbed_error = "absorbed FormList has unexpected type or flags"
            else:
                payload: bytes | memoryview = memoryview(body)[cursor:cursor + length]
                if expanded:
                    payload = _inflated(payload, expanded, "absorbed FormList")
                if len(payload) < 4:
                    absorbed_error = "absorbed FormList payload is too short"
                else:
                    members_count = _u32(payload, 0, "absorbed member count")
                    if members_count > 108 or len(payload) != 4 + members_count * 3:
                        absorbed_error = "absorbed FormList member count/length mismatch"
                    else:
                        members = []
                        for index in range(members_count):
                            member, _ = _refid(payload, 4 + 3 * index, form_ids)
                            if member is None:
                                absorbed_error = "unresolved absorbed FormList member"
                                break
                            members.append(member)
                        if absorbed_error is None:
                            absorbed_members = tuple(members)
        cursor += length
    if cursor != table3_off:
        raise SaveFormatError("ChangeForm section length/count mismatch")
    if found == 0:
        absorbed_error = "absorbed FormList ChangeForm missing"
    if plugin_warning:
        warnings.append(plugin_warning)
    if resolved_shard_count != 108:
        warnings.append(f"only {resolved_shard_count}/108 shard FormIDs resolve in the ESS array")
    if absorbed_error:
        warnings.append(absorbed_error)
    return ParsedSave(path, character, save_number, header_version, form_version,
                      resolver.index if resolver else None,
                      resolved_shard_count, absorbed_members, absorbed_error, counters, tuple(warnings),
                      player_location, catalog.plugin_kind)
