"""Read-only structural importer for Deadmano's regular and compacted ESPs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import uuid
from pathlib import Path

from .form_ids import PluginKind, plugin_local_id
from .plugin_records import PluginError, StructuredRecord, read_structured_plugin, text, xyz


VALIDATOR_VERSION = "deadmano_structural_v3"
HOLDS = ("Eastmarch", "Falkreath", "Haafingar", "Hjaalmarch", "ThePale",
         "TheReach", "TheRift", "Whiterun", "Winterhold")
HOLD_COUNTS = dict(zip(HOLDS, (12, 10, 8, 12, 10, 15, 15, 18, 8)))
SHARD_NAME = re.compile(r"DMN_Skyshard(" + "|".join(HOLDS) + r")\d{2}")
COUNTERS = ("DMN_SkyshardsCountActivated", "DMN_SkyshardsCountTotal",
            "DMN_SkyshardsSkyrimCountActivated", "DMN_SkyshardsSkyrimCountTotal")
VALIDATED_PATCHES = {
    "E50EA2C59549ACFE92CD449749996313C2B7A1BE6CF3F2E2CE09DF0A4FABDD3C",
    "A2F139E05067938B489A4785B303B7129300A663E851B5489F978C16000AFA57",
    "B4AE2EA68E122053A6A64A8FBFEC36B52CC6F40896D99E914BBA47B22FA67B2A",
}


def _form(value: bytes | None, label: str) -> int:
    if value is None or len(value) != 4:
        raise PluginError(f"Missing or malformed {label} FormID")
    return struct.unpack("<I", value)[0]


def _one(records: tuple[StructuredRecord, ...], kind: str, editor: str) -> StructuredRecord:
    found = [r for r in records if r.kind == kind and text(r.first("EDID")) == editor]
    if len(found) != 1:
        raise PluginError(f"Expected one {kind} {editor}; found {len(found)}")
    return found[0]


def _script_counter(value: bytes | None) -> int:
    """Read the sole object property on the observed shard absorption VMAD."""
    if value is None or len(value) < 20 or value[:6] != b"\x05\x00\x02\x00\x01\x00":
        raise PluginError("Unsupported or missing DMN_SkyshardsAbsorb VMAD")
    cursor = 6

    def name() -> str:
        nonlocal cursor
        if cursor + 2 > len(value):
            raise PluginError("Truncated Skyshard VMAD string")
        size = struct.unpack_from("<H", value, cursor)[0]
        cursor += 2
        if cursor + size > len(value):
            raise PluginError("Truncated Skyshard VMAD string")
        result = value[cursor:cursor + size].decode("ascii")
        cursor += size
        return result

    if name() != "DMN_SkyshardsAbsorb":
        raise PluginError("Skyshard absorption script changed")
    if value[cursor:cursor + 3] != b"\x01\x01\x00":
        raise PluginError("Skyshard script property count changed")
    cursor += 3
    if name() != "DMN_SkyshardsActivatedCounter":
        raise PluginError("Skyshard activated-counter property changed")
    if cursor + 10 != len(value) or value[cursor:cursor + 6] != b"\x01\x01\x00\x00\xff\xff":
        raise PluginError("Skyshard activated-counter property layout changed")
    return struct.unpack_from("<I", value, cursor + 6)[0]


def _base_absorbed_list(value: bytes | None) -> int:
    """Resolve the base activator script's absorbed-list object property."""
    if value is None or len(value) < 6 or value[:4] != b"\x05\x00\x02\x00":
        raise PluginError("Skyshard activator VMAD missing or unsupported")
    scripts = struct.unpack_from("<H", value, 4)[0]
    cursor = 6
    links: dict[tuple[str, str], int] = {}
    seen_scripts: set[str] = set()

    def name() -> str:
        nonlocal cursor
        if cursor + 2 > len(value):
            raise PluginError("Truncated activator VMAD string")
        size = struct.unpack_from("<H", value, cursor)[0]
        cursor += 2
        if cursor + size > len(value):
            raise PluginError("Truncated activator VMAD string")
        result = value[cursor:cursor + size].decode("ascii")
        cursor += size
        return result

    for _ in range(scripts):
        script = name()
        seen_scripts.add(script)
        if cursor + 3 > len(value):
            raise PluginError("Truncated activator script properties")
        count = struct.unpack_from("<H", value, cursor + 1)[0]
        cursor += 3
        for _ in range(count):
            prop = name()
            if cursor + 10 > len(value) or value[cursor:cursor + 6] != b"\x01\x01\x00\x00\xff\xff":
                raise PluginError("Unsupported activator script property layout")
            links[script, prop] = struct.unpack_from("<I", value, cursor + 6)[0]
            cursor += 10
    if cursor != len(value):
        raise PluginError("Activator VMAD length mismatch")
    if seen_scripts != {"DMN_SkyshardsAbsorbAnimation", "DMN_SkyshardsAbsorb"}:
        raise PluginError(f"Skyshard activator scripts changed: {seen_scripts}")
    try:
        return links["DMN_SkyshardsAbsorb", "DMN_SkyshardsAbsorbedList"]
    except KeyError as exc:
        raise PluginError("Activator no longer references absorbed FormList") from exc


def _tamriel_group(record: StructuredRecord) -> bool:
    groups = record.groups
    return (len(groups) >= 3 and groups[0] == (0, b"WRLD") and
            groups[1] == (1, struct.pack("<I", 0x3C)) and
            groups[2] == (6, struct.pack("<I", 0xD74)))


def _quest_counter_links(value: bytes | None, globals_: dict[str, StructuredRecord]) -> None:
    """Check the quest data script's four object properties by name and target ID."""
    if value is None or value[:4] != b"\x05\x00\x02\x00":
        raise PluginError("Skyshards quest data script is missing or unsupported")
    for name, record in globals_.items():
        encoded = name.encode("ascii")
        token = struct.pack("<H", len(encoded)) + encoded + b"\x01\x01\x00\x00\xff\xff"
        if value.count(token) != 1:
            raise PluginError(f"Skyshards quest data lacks a unique {name} property")
        at = value.index(token) + len(token)
        if at + 4 > len(value) or struct.unpack_from("<I", value, at)[0] != record.form_id:
            raise PluginError(f"Skyshards quest data {name} points to another global")


def export_catalog(plugin_path: Path, output: Path, patches: tuple[Path, ...] = ()) -> None:
    data, records = read_structured_plugin(plugin_path)
    header = records[0]
    masters = tuple(text(value) for value in header.all("MAST"))
    if masters != ("Skyrim.esm",):
        raise PluginError("Expected Deadmano Skyshards.esp with Skyrim.esm as its sole master")
    kind: PluginKind = "light" if header.flags & 0x200 else "full"
    self_index = len(masters)

    def local(record: StructuredRecord) -> int:
        try:
            return plugin_local_id(record.form_id, self_index=self_index, kind=kind)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

    base = _one(records, "ACTI", "DMN_Skyshard")
    absorbed = _one(records, "FLST", "DMN_SkyshardsAbsorbedList")
    globals_ = {name: _one(records, "GLOB", name) for name in COUNTERS}
    for record in (base, absorbed, *globals_.values()):
        local(record)
    if _base_absorbed_list(base.first("VMAD")) != absorbed.form_id:
        raise PluginError("Activator script points to a different absorbed FormList")
    _quest_counter_links(_one(records, "QUST", "DMN_SkyshardsQuestData").first("VMAD"), globals_)
    if tuple(kind for kind, _ in absorbed.values) != ("EDID",):
        raise PluginError("Absorbed FormList structure changed; ledger semantics unsupported")
    for name, record in globals_.items():
        value = record.first("FLTV")
        if value is None or len(value) != 4 or struct.unpack("<f", value)[0] != (
                108 if name.endswith("Total") else 0):
            raise PluginError(f"Counter {name} has unexpected initial value")

    expected_ids = {f"DMN_Skyshard{hold}{number:02d}" for hold, count in HOLD_COUNTS.items()
                    for number in range(1, count + 1)}
    shards = [r for r in records if r.kind == "REFR" and
              SHARD_NAME.fullmatch(text(r.first("EDID")) or "")]
    base_references = [r for r in records if r.kind == "REFR" and
                       r.first("NAME") == struct.pack("<I", base.form_id)]
    by_id = {text(r.first("EDID")): r for r in shards}
    if (len(shards) != 108 or len(base_references) != 108 or
            {r.form_id for r in base_references} != {r.form_id for r in shards} or
            set(by_id) != expected_ids or
            len({r.form_id for r in shards}) != 108):
        raise PluginError("Expected exactly the 108 distinct Deadmano shard EditorIDs and references")
    state_forms = {base.form_id, absorbed.form_id, *(r.form_id for r in globals_.values())}
    if len(state_forms) != 6 or state_forms & {r.form_id for r in shards}:
        raise PluginError("Shard, activator and collection-state FormIDs overlap")
    shard_forms = {r.form_id for r in shards}
    activated = globals_["DMN_SkyshardsSkyrimCountActivated"].form_id
    for editor, record in by_id.items():
        local(record)
        if (_form(record.first("NAME"), "shard base") != base.form_id or
                not _tamriel_group(record) or
                _script_counter(record.first("VMAD")) != activated):
            raise PluginError(f"Unsupported base, worldspace or script on {editor}")

    aliases = [_form(value, "quest alias") for r in records if r.kind == "QUST"
               and (text(r.first("EDID")) or "").endswith("Helper")
               for value in r.all("ALFR")]
    if len(aliases) != 108 or set(aliases) != shard_forms:
        raise PluginError("Helper quest aliases do not exactly enumerate the 108 shards")
    marker_list = _one(records, "FLST", "DMN_SkyshardsMapMarkersList")
    marker_forms = [_form(value, "map marker") for value in marker_list.all("LNAM")]
    markers = [r for r in records if r.kind == "REFR" and
               (text(r.first("EDID")) or "").endswith("MM")]
    by_marker = {text(r.first("EDID")): r for r in markers}
    if (len(marker_forms) != 108 or len(markers) != 108 or
            set(by_marker) != {editor + "MM" for editor in by_id} or
            set(marker_forms) != {r.form_id for r in markers}):
        raise PluginError("Marker FormList does not exactly enumerate shard markers")
    if any(r.first("NAME") != struct.pack("<I", 0x10) or not _tamriel_group(r) or
           xyz(dict(r.values)) is None for r in markers):
        raise PluginError("Shard map-marker geometry or base is unsupported")

    catalog = []
    for editor, record in sorted(by_id.items()):
        position = xyz(dict(record.values))
        if position is None or not all(math.isfinite(v) for v in position):
            raise PluginError(f"Invalid shard coordinates: {editor}")
        marker = by_marker[editor + "MM"]
        catalog.append({"editor_id": editor, "loaded_form_id": f"{record.form_id:08X}",
                        "loaded_local_form_id": f"{local(record):06X}",
                        "worldspace": "Tamriel", "cell": "00000D74",
                        "x": position[0], "y": position[1], "z": position[2],
                        "location_hint": text(marker.first("FULL")),
                        "coordinate_source": "Skyshards.esp"})
    by_form = {row["loaded_form_id"]: row for row in catalog}
    applied: set[str] = set()
    patch_report = []
    for patch in patches:
        patch_data, patch_records = read_structured_plugin(patch)
        digest = hashlib.sha256(patch_data).hexdigest().upper()
        if digest not in VALIDATED_PATCHES:
            raise PluginError(f"Location patch is not validated: {patch.name}")
        patch_masters = [text(v) for v in patch_records[0].all("MAST")]
        if patch_masters.count("Skyshards.esp") != 1:
            raise PluginError(f"Patch lacks Skyshards.esp master: {patch.name}")
        master_index = patch_masters.index("Skyshards.esp")
        changed = 0
        for record in patch_records:
            if record.kind != "REFR" or record.form_id >> 24 != master_index:
                continue
            plugin_form = f"{self_index:02X}{record.form_id & 0xFFFFFF:06X}"
            if plugin_form not in by_form:
                continue
            if plugin_form in applied:
                raise PluginError(f"Duplicate shard override in {patch.name}")
            row = by_form[plugin_form]
            position = xyz(dict(record.values))
            if text(record.first("EDID")) != row["editor_id"] or position is None:
                raise PluginError(f"Invalid shard override in {patch.name}")
            row.update(x=position[0], y=position[1], z=position[2],
                       coordinate_source=patch.name)
            applied.add(plugin_form)
            changed += 1
        if changed != 1:
            raise PluginError(f"Patch {patch.name} does not override exactly one imported shard")
        patch_report.append({"name": patch.name, "sha256": digest,
                             "source_path": str(patch.resolve())})

    catalog_text = json.dumps(catalog, indent=2)
    identity = {"validation_kind": VALIDATOR_VERSION,
                "loaded_sha256": hashlib.sha256(data).hexdigest().upper(),
                "loaded_esl_flag": kind == "light", "plugin_kind": kind,
                "plugin_masters": masters, "self_index": self_index,
                "source_path": str(plugin_path.resolve()), "shard_count": 108,
                "absorbed_list_form_id": f"{absorbed.form_id:08X}",
                "counter_global_form_ids": {name: f"{r.form_id:08X}"
                                            for name, r in globals_.items()},
                "location_patches": patch_report,
                "catalog_sha256": hashlib.sha256(catalog_text.encode()).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    for target, content in ((output, catalog_text),
                            (output.with_name("identity-validation.json"),
                             json.dumps(identity, indent=2))):
        temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content.encode())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def ensure_catalog(game_dir: Path, output: Path) -> None:
    identity_path = output.with_name("identity-validation.json")
    data_plugin = game_dir / "Data/Skyshards.esp"
    if output.is_file() and identity_path.is_file():
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            identity = {}
        if isinstance(identity, dict):
            source = Path(identity.get("source_path", ""))
            if identity.get("validation_kind") == VALIDATOR_VERSION:
                from .catalog import CatalogError, load_catalog
                try:
                    load_catalog(output)
                except CatalogError as exc:
                    raise PluginError(f"Imported catalog is invalid; run import-plugin again: {exc}") from exc
                for patch in identity.get("location_patches", []):
                    path = Path(patch.get("source_path", ""))
                    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest().upper() != patch.get("sha256"):
                        raise PluginError(f"Imported location patch changed: {path}; run import-plugin again")
                if not source.is_file():
                    return  # A validated imported cache remains usable after source removal.
                if hashlib.sha256(source.read_bytes()).hexdigest().upper() == identity.get("loaded_sha256"):
                    return
                if source.resolve() == data_plugin.resolve():
                    export_catalog(data_plugin, output)
                    return
                raise PluginError("Imported Skyshards.esp changed; run import-plugin again")
    if not data_plugin.is_file():
        raise PluginError("Skyshards.esp was not found in the configured game Data folder. "
                          "Use 'uv run skyshard-tracker import-plugin <path-to-Skyshards.esp>'.")
    export_catalog(data_plugin, output)
