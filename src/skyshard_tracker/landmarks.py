"""Curated vanilla map marker references, generated from the user's Skyrim.esm."""

from __future__ import annotations

import json
import hashlib
import math
import os
import uuid
from pathlib import Path

from .plugin_records import PluginError, read_plugin, text, xyz


SITES = {
    "0001C38A": ("Markarth", "city"), "0004D0F4": ("Solitude", "city"),
    "000162CE": ("Whiterun", "city"), "0001C390": ("Riften", "city"),
    "00038436": ("Windhelm", "city"), "0001773A": ("Dawnstar", "city"),
    "00017760": ("Falkreath", "city"), "000177B0": ("Morthal", "city"),
    "000177EF": ("Winterhold", "city"), "000162A4": ("Riverwood", "settlement"),
    "00016352": ("High Hrothgar", "landmark"),
    "00016232": ("Bleak Falls Barrow", "landmark"),
    "000C44AD": ("Dragonsreach", "landmark"),
    "00046BDF": ("College of Winterhold", "landmark"),
    "00017791": ("Ivarstead", "settlement"),
    "000177CC": ("Rorikstead", "settlement"),
    "00017753": ("Dragon Bridge", "settlement"),
    "000177D7": ("Shor's Stone", "settlement"),
    "000177A1": ("Kynesgrove", "settlement"),
    "0001779A": ("Karthwasten", "settlement"),
    "00017732": ("Darkwater Crossing", "settlement"),
    "000162A5": ("Saarthal", "landmark"),
    "0001624A": ("Fort Greymoor", "landmark"),
    "00016251": ("Fort Snowhawk", "landmark"),
}


def build_landmarks(game_dir: Path, output: Path, *, force: bool = False) -> dict:
    source = game_dir / "Data/Skyrim.esm"
    content, records = read_plugin(source)
    digest = hashlib.sha256(content).hexdigest()
    if output.is_file() and not force:
        try:
            cached = json.loads(output.read_text(encoding="utf-8"))
            features = cached["features"]
            if (cached.get("generator_version") == 1 and cached.get("source_sha256") == digest and
                    len(features) == len(SITES) and
                    {row["form_id"] for row in features} == set(SITES) and
                    all((row["name"], row["kind"]) == SITES[row["form_id"]] and
                        row["winning_plugin"] == "Skyrim.esm" and
                        all(math.isfinite(row[key]) for key in ("x", "y", "z")) for row in features)):
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass
    found = {}
    for kind, form_id, _, values in records:
        if kind != "REFR" or form_id not in SITES:
            continue
        position = xyz(values)
        if position is None:
            raise PluginError(f"Missing position for vanilla marker {form_id}")
        name, category = SITES[form_id]
        found[form_id] = dict(form_id=form_id, editor_id=text(values.get("EDID")),
                              name=name, kind=category, x=position[0], y=position[1],
                              z=position[2], winning_plugin="Skyrim.esm")
    missing = SITES.keys() - found.keys()
    if missing:
        raise PluginError(f"Skyrim.esm is missing curated map markers: {sorted(missing)}")
    payload = {"source": "Skyrim.esm", "source_sha256": digest,
               "source_size": len(content), "generator_version": 1,
               "features": [found[key] for key in SITES]}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(json.dumps(payload, indent=2).encode("utf-8"))
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return payload
