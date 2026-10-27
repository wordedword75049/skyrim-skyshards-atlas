"""Load the locally validated Deadmano Skyshards catalog."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from types import MappingProxyType

from .models import Catalog, Coordinates, ShardDefinition
from .catalog_import import VALIDATOR_VERSION
from .form_ids import PluginKind, plugin_local_id


class CatalogError(ValueError):
    """The local verified catalog is missing or inconsistent."""


HOLD_NAMES = {
    "Eastmarch": "Eastmarch", "Falkreath": "Falkreath", "Haafingar": "Haafingar",
    "Hjaalmarch": "Hjaalmarch", "ThePale": "The Pale", "TheReach": "The Reach",
    "TheRift": "The Rift", "Whiterun": "Whiterun", "Winterhold": "Winterhold",
}
SHARD_EDITOR_ID = re.compile(r"^DMN_Skyshard(Eastmarch|Falkreath|Haafingar|Hjaalmarch|ThePale|TheReach|TheRift|Whiterun|Winterhold)\d{2}$")
COUNTER_NAMES = {
    "DMN_SkyshardsCountActivated", "DMN_SkyshardsSkyrimCountActivated",
    "DMN_SkyshardsCountTotal", "DMN_SkyshardsSkyrimCountTotal",
}


def _local_form_id(raw: str, *, kind: PluginKind, self_index: int) -> int:
    try:
        return plugin_local_id(int(raw, 16), self_index=self_index, kind=kind)
    except (ValueError, TypeError) as exc:
        raise CatalogError(f"invalid {kind} Skyshards.esp FormID: {raw}") from exc


def load_catalog(path: Path) -> Catalog:
    """Read the ignored local plugin export; never edit a plugin or save."""
    identity_path = path.with_name("identity-validation.json")
    try:
        catalog_bytes = path.read_bytes()
        identity_bytes = identity_path.read_bytes()
        rows = json.loads(catalog_bytes)
        identity = json.loads(identity_bytes)
    except (OSError, ValueError) as exc:
        raise CatalogError(f"cannot read verified catalog: {exc}") from exc
    if not isinstance(identity, dict) or not isinstance(rows, list) or len(rows) != 108 or identity.get("shard_count") != 108:
        raise CatalogError("verified catalog must contain exactly 108 shard records")
    if identity.get("validation_kind") != VALIDATOR_VERSION:
        raise CatalogError("catalog must be reimported with the structural Deadmano validator")
    kind = identity.get("plugin_kind")
    if kind not in ("full", "light") or identity.get("loaded_esl_flag") != (kind == "light"):
        raise CatalogError("catalog plugin kind and TES4 ESL flag disagree")
    if identity.get("plugin_masters") != ["Skyrim.esm"] or identity.get("self_index") != 1:
        raise CatalogError("catalog plugin master identity is unsupported")
    digest = identity.get("loaded_sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9A-F]{64}", digest):
        raise CatalogError("identity report has no valid loaded-plugin hash")
    if identity.get("catalog_sha256") != hashlib.sha256(catalog_bytes).hexdigest():
        raise CatalogError("catalog and identity report disagree; rerun import-plugin")
    try:
        absorbed_id = _local_form_id(identity["absorbed_list_form_id"], kind=kind, self_index=1)
        counters = {name: _local_form_id(raw, kind=kind, self_index=1)
                    for name, raw in identity["counter_global_form_ids"].items()}
        if set(counters) != COUNTER_NAMES:
            raise CatalogError("identity report has unexpected counter FormIDs")
        shards = []
        for row in rows:
            editor_id = row["editor_id"]
            match = SHARD_EDITOR_ID.fullmatch(editor_id)
            if not match:
                raise CatalogError(f"invalid shard EditorID: {editor_id}")
            loaded_id = _local_form_id(row["loaded_form_id"], kind=kind, self_index=1)
            if loaded_id != int(row["loaded_local_form_id"], 16):
                raise CatalogError(f"local FormID mismatch for {editor_id}")
            xyz = (row["x"], row["y"], row["z"])
            if any(isinstance(value, bool) or not math.isfinite(value) for value in xyz):
                raise CatalogError(f"non-finite coordinate for {editor_id}")
            if row["worldspace"] != "Tamriel" or row["cell"] != "00000D74":
                raise CatalogError(f"unexpected placement for {editor_id}")
            shards.append(ShardDefinition(
                id=editor_id, hold=HOLD_NAMES[match.group(1)], worldspace=row["worldspace"],
                cell=row["cell"], coordinates=Coordinates(x=xyz[0], y=xyz[1], z=xyz[2]),
                loaded_local_id=loaded_id, loaded_form_id=row["loaded_form_id"],
                original_form_id=row.get("original_form_id"), location_hint=row.get("location_hint"),
                coordinate_source=row["coordinate_source"],
            ))
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError(f"malformed verified catalog: {exc}") from exc
    by_id = {shard.id: shard for shard in shards}
    by_local_id = {shard.loaded_local_id: shard for shard in shards}
    if len(by_id) != 108 or len(by_local_id) != 108:
        raise CatalogError("duplicate shard EditorID or local FormID")
    version = hashlib.sha256(catalog_bytes + identity_bytes).hexdigest()
    return Catalog(tuple(shards), MappingProxyType(by_id), MappingProxyType(by_local_id),
                   version, digest, absorbed_id, MappingProxyType(counters), kind)
