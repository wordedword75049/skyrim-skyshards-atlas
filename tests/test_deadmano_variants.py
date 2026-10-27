"""Real ESP structure checks; private game inputs remain outside Git."""

from __future__ import annotations

import hashlib
import json
import dataclasses
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from skyshard_tracker.catalog import load_catalog
from skyshard_tracker.catalog_import import ensure_catalog, export_catalog
from skyshard_tracker.ess import parse_save
from skyshard_tracker.plugin_records import PluginError
from skyshard_tracker.progress import classify
from tests.test_public_progress import synthetic_ess


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "research/inputs/Skyshards.esp"
COMPACTED = ROOT / "research/inputs/Skyshards-loaded.esp"
PATCHES = tuple(ROOT / "research/inputs" / name for name in (
    "Skyshards - Ryn's Bleakwind Basin Patch.esp",
    "Skyshards - Ryn's Karthspire Patch.esp",
    "Skyshards - TGC Winterhold Patch.esp"))


@unittest.skipUnless(ORIGINAL.is_file() and COMPACTED.is_file(),
                     "Private original and compacted ESPs are unavailable")
class VariantIntegrationTests(unittest.TestCase):
    def _import(self, path: Path, directory: Path):
        output = directory / "effective-shards.json"
        export_catalog(path, output)
        return load_catalog(output)

    def test_both_real_plugins_have_matching_logical_shards_and_positions(self):
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in
                  (ORIGINAL, COMPACTED)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self._import(ORIGINAL, root / "full")
            compacted = self._import(COMPACTED, root / "light")
        self.assertEqual((original.plugin_kind, compacted.plugin_kind), ("full", "light"))
        self.assertEqual(set(original.by_id), set(compacted.by_id))
        self.assertEqual(len(original.by_id), 108)
        self.assertEqual({name: shard.coordinates for name, shard in original.by_id.items()},
                         {name: shard.coordinates for name, shard in compacted.by_id.items()})
        self.assertEqual(original.by_id["DMN_SkyshardFalkreath04"].loaded_form_id, "0102C037")
        self.assertEqual(compacted.by_id["DMN_SkyshardFalkreath04"].loaded_form_id, "01000881")
        self.assertEqual(original.absorbed_list_local_id, 0x17CB3)
        self.assertEqual(compacted.absorbed_list_local_id, 0x80A)
        self.assertEqual(set(original.counter_local_ids), set(compacted.counter_local_ids))
        self.assertEqual(hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in hashes})

    def test_original_plugin_with_synthetic_full_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._import(ORIGINAL, root / "catalog")
            names = tuple(catalog.by_id)[:22]
            for index in (4, 77):
                path = root / f"full-{index}.ess"
                path.write_bytes(synthetic_ess(catalog, names, full_index=index))
                parsed = parse_save(path, catalog)
                result = classify(parsed, catalog,
                                  modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                self.assertEqual((result.collected_count, result.uncollected_count,
                                  result.unknown_count), (22, 86, 0))
                self.assertEqual(parsed.skyshards_index, index)
                self.assertEqual(parsed.skyshards_kind, "full")
                inconsistent = dataclasses.replace(parsed, counters={
                    **parsed.counters, "DMN_SkyshardsCountActivated": 21.0})
                uncertain = classify(inconsistent, catalog,
                                     modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                self.assertEqual((uncertain.collected_count, uncertain.uncollected_count,
                                  uncertain.unknown_count), (22, 0, 86))

    @unittest.skipUnless(all(path.is_file() for path in PATCHES),
                         "Private validated location patches are unavailable")
    def test_compacted_location_patches_match_prior_effective_coordinates(self):
        expected = {row["editor_id"]: (row["x"], row["y"], row["z"])
                    for row in json.loads((ROOT / "research/outputs/effective-shards.json").read_text())}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "effective-shards.json"
            export_catalog(COMPACTED, output, PATCHES)
            catalog = load_catalog(output)
        self.assertEqual({name: (s.coordinates.x, s.coordinates.y, s.coordinates.z)
                          for name, s in catalog.by_id.items()}, expected)

    def test_truncated_duplicate_missing_and_changed_state_records_rejected(self):
        raw = ORIGINAL.read_bytes()
        counter_name = b"DMN_SkyshardsCountActivated"
        counter_link = (struct.pack("<H", len(counter_name)) + counter_name +
                        bytes.fromhex("01010000ffff03a0de01"))
        self.assertIn(counter_link, raw)
        mutations = (
            raw[:70],
            raw.replace(b"DMN_SkyshardFalkreath04\x00",
                        b"DMN_SkyshardFalkreath03\x00", 1),
            raw.replace(b"DMN_SkyshardFalkreath04\x00",
                        b"XMN_SkyshardFalkreath04\x00", 1),
            raw.replace(b"DMN_SkyshardsAbsorbedList\x00",
                        b"XMN_SkyshardsAbsorbedList\x00", 1),
            raw.replace(b"DMN_SkyshardsCountTotal\x00",
                        b"XMN_SkyshardsCountTotal\x00", 1),
            raw.replace(counter_link, counter_link[:-4] + bytes.fromhex("04a0de01"), 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, payload in enumerate(mutations):
                with self.subTest(index=index):
                    source = root / f"bad-{index}.esp"
                    source.write_bytes(payload)
                    with self.assertRaises(PluginError):
                        export_catalog(source, root / f"out-{index}/catalog.json")

    def test_cache_detects_plugin_change_and_keeps_game_copy_unmodified(self):
        raw = ORIGINAL.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            (game / "Data").mkdir(parents=True)
            source = root / "manually-imported.esp"
            source.write_bytes(raw)
            output = root / "catalog/effective-shards.json"
            export_catalog(source, output)
            first = json.loads(output.with_name("identity-validation.json").read_text())
            self.assertEqual(first["loaded_sha256"], hashlib.sha256(raw).hexdigest().upper())
            ensure_catalog(game, output)
            self.assertEqual(source.read_bytes(), raw)
            source.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            with self.assertRaisesRegex(PluginError, "changed"):
                ensure_catalog(game, output)
            self.assertEqual(json.loads(output.with_name("identity-validation.json").read_text()),
                             first)

    def test_automatic_data_discovery_reimports_compatible_changed_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            (game / "Data").mkdir(parents=True)
            source = game / "Data/Skyshards.esp"
            source.write_bytes(ORIGINAL.read_bytes())
            output = root / "catalog/effective-shards.json"
            ensure_catalog(game, output)
            self.assertEqual(load_catalog(output).plugin_kind, "full")
            source.write_bytes(COMPACTED.read_bytes())
            ensure_catalog(game, output)
            self.assertEqual(load_catalog(output).plugin_kind, "light")
            self.assertEqual(source.read_bytes(), COMPACTED.read_bytes())


if __name__ == "__main__":
    unittest.main()
