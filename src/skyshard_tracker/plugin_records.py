"""Bounded structural reader for the small set of TES4 records we need."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class PluginError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StructuredRecord:
    kind: str
    form_id: int
    flags: int
    values: tuple[tuple[str, bytes], ...]
    groups: tuple[tuple[int, bytes], ...]

    def first(self, name: str) -> bytes | None:
        return next((value for kind, value in self.values if kind == name), None)

    def all(self, name: str) -> tuple[bytes, ...]:
        return tuple(value for kind, value in self.values if kind == name)


def fields(data: bytes) -> Iterator[tuple[str, bytes]]:
    offset = 0
    extended = None
    while offset < len(data):
        if offset + 6 > len(data):
            raise PluginError("Truncated plugin subrecord")
        kind = data[offset:offset + 4].decode("ascii")
        size = struct.unpack_from("<H", data, offset + 4)[0]
        offset += 6
        if kind == "XXXX":
            if size != 4 or offset + 4 > len(data):
                raise PluginError("Invalid extended subrecord size")
            extended = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            continue
        if extended is not None:
            size, extended = extended, None
        if offset + size > len(data):
            raise PluginError("Plugin subrecord exceeds record boundary")
        yield kind, data[offset:offset + size]
        offset += size


def records(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    offset = start
    while offset < end:
        if offset + 24 > end:
            raise PluginError("Truncated plugin record header")
        kind = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        stop = offset + (size if kind == b"GRUP" else 24 + size)
        if stop > end or stop <= offset or (kind == b"GRUP" and size < 24):
            raise PluginError("Invalid plugin record or group length")
        if kind == b"GRUP":
            yield from records(data, offset + 24, stop)
        else:
            flags, form_id = struct.unpack_from("<II", data, offset + 8)
            payload = data[offset + 24:stop]
            if flags & 0x40000:
                if len(payload) < 4:
                    raise PluginError("Truncated compressed plugin record")
                declared = struct.unpack_from("<I", payload)[0]
                payload = zlib.decompress(payload[4:])
                if len(payload) != declared:
                    raise PluginError("Compressed plugin record length mismatch")
            yield kind.decode("ascii"), f"{form_id:08X}", flags, dict(fields(payload))
        offset = stop


def text(value: bytes | None) -> str | None:
    return value.rstrip(b"\0").decode("cp1252", errors="replace") if value else None


def xyz(record_fields: dict[str, bytes]) -> tuple[float, float, float] | None:
    value = record_fields.get("DATA")
    return struct.unpack_from("<3f", value) if value is not None and len(value) == 24 else None


def read_plugin(path: Path):
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PluginError(f"Cannot read plugin {path}: {exc}") from exc
    if len(data) < 24 or data[:4] != b"TES4":
        raise PluginError(f"Not a TES4 plugin: {path}")
    def checked_records():
        try:
            yield from records(data)
        except (struct.error, zlib.error, UnicodeError) as exc:
            raise PluginError(f"Malformed plugin {path}: {exc}") from exc
    return data, checked_records()


def structured_records(data: bytes, start: int = 0, end: int | None = None,
                       groups: tuple[tuple[int, bytes], ...] = ()) -> Iterator[StructuredRecord]:
    """Preserve duplicate subrecords and group ancestry for identity validation."""
    end = len(data) if end is None else end
    offset = start
    while offset < end:
        if offset + 24 > end:
            raise PluginError("Truncated plugin record header")
        kind = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        stop = offset + (size if kind == b"GRUP" else 24 + size)
        if stop > end or stop <= offset or (kind == b"GRUP" and size < 24):
            raise PluginError("Invalid plugin record or group length")
        if kind == b"GRUP":
            label = data[offset + 8:offset + 12]
            group_type = struct.unpack_from("<i", data, offset + 12)[0]
            yield from structured_records(data, offset + 24, stop,
                                          groups + ((group_type, label),))
        else:
            flags, form_id = struct.unpack_from("<II", data, offset + 8)
            payload = data[offset + 24:stop]
            if flags & 0x40000:
                if len(payload) < 4:
                    raise PluginError("Truncated compressed plugin record")
                declared = struct.unpack_from("<I", payload)[0]
                if declared > 64 * 1024 * 1024:
                    raise PluginError("Compressed plugin record is too large")
                decoder = zlib.decompressobj()
                payload = decoder.decompress(payload[4:], declared + 1)
                if len(payload) != declared or not decoder.eof or decoder.unused_data:
                    raise PluginError("Compressed plugin record length mismatch")
            yield StructuredRecord(kind.decode("ascii"), form_id, flags,
                                   tuple(fields(payload)), groups)
        offset = stop


def read_structured_plugin(path: Path) -> tuple[bytes, tuple[StructuredRecord, ...]]:
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise PluginError(f"TES4 plugin exceeds 64 MiB safety limit: {path}")
        data = path.read_bytes()
        if len(data) < 24 or len(data) > 64 * 1024 * 1024 or data[:4] != b"TES4":
            raise PluginError(f"Not a supported TES4 plugin: {path}")
        parsed = tuple(structured_records(data))
    except OSError as exc:
        raise PluginError(f"Cannot read plugin {path}: {exc}") from exc
    except (struct.error, zlib.error, UnicodeError) as exc:
        raise PluginError(f"Malformed plugin {path}: {exc}") from exc
    if not parsed or parsed[0].kind != "TES4" or sum(record.kind == "TES4" for record in parsed) != 1:
        raise PluginError("Plugin must have exactly one TES4 header")
    return data, parsed
