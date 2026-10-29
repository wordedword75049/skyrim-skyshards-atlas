"""Distributable synthetic tests for the safety gates; real ESS fixtures stay ignored."""

from __future__ import annotations

import json
import hashlib
from importlib.metadata import version as package_version
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import lz4.block
from fastapi.testclient import TestClient

from skyshard_tracker.api import create_app
from skyshard_tracker.catalog_import import VALIDATOR_VERSION
from skyshard_tracker.ess import (ParsedSave, SaveFormatError, UnsupportedSaveError,
                                  parse_save, unavailable_player_location)
from skyshard_tracker.models import Catalog, Coordinates, ShardDefinition, TrackerConfig
from skyshard_tracker.progress import classify
from skyshard_tracker.watcher import SaveWatcher


COUNTERS = ("DMN_SkyshardsCountActivated", "DMN_SkyshardsSkyrimCountActivated",
            "DMN_SkyshardsCountTotal", "DMN_SkyshardsSkyrimCountTotal")
HOLDS = ("Eastmarch", "Falkreath", "Haafingar", "Hjaalmarch", "ThePale",
         "TheReach", "TheRift", "Whiterun", "Winterhold")


def synthetic_catalog(kind: str = "light") -> Catalog:
    shards = []
    for hold in HOLDS:
        for number in range(1, 13):
            local = (0xA00 if kind == "light" else 0x1A000) + len(shards)
            shards.append(ShardDefinition(
                id=f"DMN_Skyshard{hold}{number:02d}", hold=hold, worldspace="Tamriel",
                cell="00000D74", coordinates=Coordinates(x=0, y=0, z=0),
                loaded_local_id=local, loaded_form_id=f"{(1 << 24) | local:08X}",
                original_form_id=None, location_hint=None, coordinate_source="synthetic"))
    return Catalog(tuple(shards), MappingProxyType({s.id: s for s in shards}),
                   MappingProxyType({s.loaded_local_id: s for s in shards}),
                   "synthetic", "0" * 64, 0x80A if kind == "light" else 0x17CB3,
                   MappingProxyType({name: (0x98A if kind == "light" else 0x1EA003) + index
                                     for index, name in enumerate(COUNTERS)}), kind)


def parsed(members: tuple[int, ...] | None, *, count: int | None = None,
           light_index: int | None = 0x456, resolved: int = 108) -> ParsedSave:
    counters = {name: count if index < 2 else 108 for index, name in enumerate(COUNTERS)}
    return ParsedSave(Path("synthetic.ess"), "TestHero", 1, 12, 78, light_index,
                      resolved, members, None if members is not None else "missing list",
                      counters, (), unavailable_player_location("synthetic"))


def synthetic_ess(catalog: Catalog, member_ids: tuple[str, ...], *, light_index: int = 0,
                  full_index: int = 7, cell_id: int = 0x3C,
                  location: str = "Skyrim", master_name: str = "Skyrim.esm",
                  player_payload: bytes | None = None) -> bytes:
    """Construct a tiny legal ESS v12/LZ4 byte stream without game-derived content."""
    def wstring(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return struct.pack("<H", len(encoded)) + encoded

    header_fields = (wstring("TestHero") + struct.pack("<I", 1) + wstring(location) +
                     wstring("1.01") + wstring("Nord") + bytes(18) +
                     struct.pack("<IIH", 1, 1, 2))
    header = (b"TESV_SAVEGAME" + struct.pack("<III", 8 + len(header_fields), 12, 1) +
              header_fields + bytes(4))
    header_size = len(header)
    if catalog.plugin_kind == "light":
        plugin_info = (b"\x01" + wstring(master_name) + struct.pack("<H", light_index + 1) +
                       b"".join(wstring(f"Other{index}.esl") for index in range(light_index)) +
                       wstring("Skyshards.esp"))
        prefix = 0xFE000000 | light_index << 12
    else:
        plugin_info = (bytes([full_index + 1]) + wstring(master_name) +
                       b"".join(wstring(f"Other{index}.esp") for index in range(1, full_index)) +
                       wstring("Skyshards.esp") + struct.pack("<H", 0))
        prefix = full_index << 24
    shard_forms = [prefix | shard.loaded_local_id for shard in catalog.shards]
    global_forms = [prefix | catalog.counter_local_ids[name] for name in COUNTERS]
    form_ids = shard_forms + global_forms + [prefix | catalog.absorbed_list_local_id]
    indexes = {form: index + 1 for index, form in enumerate(form_ids)}
    def ref(form: int) -> bytes:
        return indexes[form].to_bytes(3, "big")
    def direct(form: int) -> bytes:
        return ((1 << 22) | form).to_bytes(3, "big")

    members = [catalog.by_id[name].loaded_local_id | prefix for name in member_ids]
    globals_data = bytes([4 << 2]) + b"".join(
        ref(form) + struct.pack("<f", len(members) if index < 2 else 108)
        for index, form in enumerate(global_forms))
    table1 = struct.pack("<II", 3, len(globals_data)) + globals_data
    list_payload = struct.pack("<I", len(members)) + b"".join(ref(form) for form in members)
    list_change = (ref(prefix | catalog.absorbed_list_local_id) +
                   struct.pack("<IBB", 0x80000000, 43, 78) +
                   bytes([len(list_payload), 0]) + list_payload)
    if player_payload is None:
        player_payload = direct(cell_id) + struct.pack("<6f", 100, 200, 300, 0, 0, 1)
    player_change = (direct(0x14) + struct.pack("<IBB", 2, 1, 78) +
                     bytes([len(player_payload), 0]) + player_payload)
    changes = list_change + player_change
    form_array = struct.pack("<I", len(form_ids)) + struct.pack(f"<{len(form_ids)}I", *form_ids)
    start = 5 + len(plugin_info)
    table1_off = start + 100
    table2_off = table1_off + len(table1)
    changes_off = table2_off
    table3_off = changes_off + len(changes)
    form_array_off = table3_off
    unknown_off = form_array_off + len(form_array)
    locations = (form_array_off, unknown_off, table1_off, table2_off, changes_off, table3_off)
    location_table = struct.pack("<25I", *(tuple(header_size + value for value in locations) +
                                           (1, 0, 0, 2) + (0,) * 15))
    body = (b"\x4e" + struct.pack("<I", len(plugin_info)) + plugin_info + location_table +
            table1 + changes + form_array)
    compressed = lz4.block.compress(body, store_size=False)
    return header + struct.pack("<II", len(body), len(compressed)) + compressed


class PublicClassificationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = synthetic_catalog()
        self.prefix = 0xFE456000
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_complete_ledger_classifies_every_shard(self):
        members = tuple(self.prefix | s.loaded_local_id for s in self.catalog.shards[:22])
        result = classify(parsed(members, count=22), self.catalog, modified_at=self.now)
        self.assertEqual((result.collected_count, result.uncollected_count, result.unknown_count), (22, 86, 0))

    def test_missing_duplicate_foreign_and_bad_counter_do_not_create_false_negatives(self):
        members = tuple(self.prefix | s.loaded_local_id for s in self.catalog.shards[:2])
        for input_save in (parsed(None, count=0), parsed(members + members[:1], count=3),
                           parsed(members + (self.prefix | 0xFFF,), count=3),
                           parsed(members, count=5), parsed(members, count=2, resolved=107),
                           parsed(members, count=2, light_index=None)):
            with self.subTest(input_save=input_save):
                result = classify(input_save, self.catalog, modified_at=self.now)
                self.assertEqual(result.uncollected_count, 0)
                self.assertGreater(result.unknown_count, 0)

    def test_malformed_ess_rejected_without_modifying_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.ess"
            path.write_bytes(b"TESV_SAVEGAME")
            before = path.read_bytes()
            with self.assertRaises(SaveFormatError):
                parse_save(path, self.catalog)
            self.assertEqual(path.read_bytes(), before)

    def test_supported_and_unsupported_ess_format_boundaries(self):
        source = synthetic_ess(self.catalog, ())
        header_end = 17 + struct.unpack_from("<I", source, 13)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "format.ess"

            def check(payload: bytes, error: type[Exception] | None = None,
                      message: str | None = None):
                path.write_bytes(payload)
                if error is None:
                    parsed_save = parse_save(path, self.catalog)
                    self.assertEqual((parsed_save.header_version, parsed_save.form_version), (12, 78))
                else:
                    with self.assertRaisesRegex(error, message or ""):
                        parse_save(path, self.catalog)
                self.assertEqual(path.read_bytes(), payload)

            check(source)
            changed = bytearray(source)
            struct.pack_into("<I", changed, 17, 13)
            check(bytes(changed), UnsupportedSaveError, "header version 13; expected 12")
            changed = bytearray(source)
            struct.pack_into("<H", changed, header_end - 2, 1)
            check(bytes(changed), UnsupportedSaveError, "compression 1; expected LZ4")
            packed_start = header_end + 4 + 8
            unpacked, packed = struct.unpack_from("<II", source, header_end + 4)
            body = bytearray(lz4.block.decompress(source[packed_start:packed_start + packed],
                                                   uncompressed_size=unpacked))
            body[0] = 77
            compressed = lz4.block.compress(bytes(body), store_size=False)
            changed = source[:header_end + 4] + struct.pack("<II", len(body), len(compressed)) + compressed
            check(changed, UnsupportedSaveError, "form version 77; expected 78")
            for payload in (source[:12], source[:25], source[:header_end - 1]):
                check(payload, SaveFormatError, "signature|header")

    def test_synthetic_ess_decodes_transition_at_two_light_plugin_indices(self):
        before_ids = tuple(shard.id for shard in self.catalog.shards
                           if shard.id != "DMN_SkyshardFalkreath04")[:2]
        after_ids = before_ids + ("DMN_SkyshardFalkreath04",)
        with tempfile.TemporaryDirectory() as directory:
            snapshots = []
            for index, (names, light) in enumerate(((before_ids, 0), (after_ids, 9))):
                path = Path(directory) / f"synthetic-{index}.ess"
                content = synthetic_ess(self.catalog, names, light_index=light)
                path.write_bytes(content)
                decoded = parse_save(path, self.catalog)
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(decoded.skyshards_light_index, light)
                self.assertEqual(decoded.resolved_shard_count, 108)
                self.assertEqual(decoded.player_location.status, "available")
                snapshots.append(classify(decoded, self.catalog, modified_at=self.now))
        self.assertEqual([(row.collected_count, row.uncollected_count) for row in snapshots],
                         [(2, 106), (3, 105)])
        changed = [after.id for before, after in zip(snapshots[0].shards, snapshots[1].shards)
                   if before.status != after.status]
        self.assertEqual(changed, ["DMN_SkyshardFalkreath04"])

    def test_synthetic_ess_interior_hides_player(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "interior.ess"
            path.write_bytes(synthetic_ess(self.catalog, (), cell_id=0x222, location="Test Interior"))
            decoded = parse_save(path, self.catalog)
            self.assertEqual(decoded.player_location.status, "unavailable")
            self.assertEqual(decoded.player_location.current_cell_form_id, "00000222")

    def test_tamriel_player_position_ignores_localized_header_label(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localized.ess"
            for label in ("Skyrim", "Скайрим", "Geographic label in another language"):
                with self.subTest(label=label):
                    path.write_bytes(synthetic_ess(self.catalog, (), location=label))
                    position = parse_save(path, self.catalog).player_location
                    self.assertEqual(position.status, "available")
                    self.assertEqual(position.saved_location, label)
                    self.assertEqual(position.current_cell_form_id, "0000003C")
                    self.assertEqual((position.worldspace, position.is_exterior), ("Tamriel", True))
                    self.assertEqual((position.x, position.y, position.z, position.rotation),
                                     (100.0, 200.0, 300.0, 1.0))
                    self.assertFalse(any("header" in evidence.lower() for evidence in position.evidence))

    def test_player_position_rejects_other_cells_and_invalid_structural_evidence(self):
        valid_position = (1 << 22 | 0x3C).to_bytes(3, "big") + struct.pack("<6f", 100, 200, 300, 0, 0, 1)
        cases = (
            (dict(cell_id=0x222, location="Skyrim"), "not Tamriel"),
            (dict(cell_id=0x3D, location="Skyrim"), "not Tamriel"),
            (dict(master_name="Other.esm"), "Skyrim.esm is not the first full plugin"),
            (dict(player_payload=valid_position[:8]), "truncated"),
            (dict(player_payload=b"\x00\x00\x00" + valid_position[3:]), "cannot be resolved"),
            (dict(player_payload=valid_position[:3] + struct.pack("<6f", float("nan"), 200, 300, 0, 0, 1)),
             "non-finite"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-location.ess"
            for options, reason in cases:
                with self.subTest(options=options):
                    path.write_bytes(synthetic_ess(self.catalog, (), **options))
                    position = parse_save(path, self.catalog).player_location
                    self.assertEqual(position.status, "unavailable")
                    self.assertIn(reason, position.reason)
                    self.assertIsNone(position.x)
                    self.assertIsNone(position.worldspace)

    def test_synthetic_full_esp_remaps_at_two_full_indices(self):
        catalog = synthetic_catalog("full")
        with tempfile.TemporaryDirectory() as directory:
            for index in (3, 42):
                path = Path(directory) / f"full-{index}.ess"
                path.write_bytes(synthetic_ess(catalog, (catalog.shards[0].id,), full_index=index))
                parsed_save = parse_save(path, catalog)
                snapshot = classify(parsed_save, catalog, modified_at=self.now)
                self.assertEqual((snapshot.collected_count, snapshot.uncollected_count), (1, 107))
                self.assertEqual(snapshot.shards[0].save_form_id,
                                 f"{(index << 24) | catalog.shards[0].loaded_local_id:08X}")

    def test_esp_esl_mismatch_remains_unknown(self):
        full_catalog = synthetic_catalog("full")
        light_catalog = synthetic_catalog("light")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mismatch.ess"
            path.write_bytes(synthetic_ess(light_catalog, ()))
            snapshot = classify(parse_save(path, full_catalog), full_catalog, modified_at=self.now)
            self.assertEqual(snapshot.unknown_count, 108)
            self.assertTrue(any("but the imported plugin is full" in w for w in snapshot.warnings))

    def test_missing_optional_skse_cosave_does_not_warn_or_degrade_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Quicksave.ess"
            path.write_bytes(synthetic_ess(self.catalog, (self.catalog.shards[0].id,)))
            watcher = SaveWatcher(Path(directory), self.catalog, stability_delay=0)
            snapshot = watcher.refresh()
            self.assertEqual((snapshot.collected_count, snapshot.uncollected_count,
                              snapshot.unknown_count), (1, 107, 0))
            self.assertEqual(snapshot.save.co_save_status, "missing")
            self.assertTrue(snapshot.is_newest)
            self.assertFalse(any("SKSE" in warning for warning in snapshot.warnings))
            with patch.object(watcher, "_co_save_status", return_value="unreadable"):
                unreadable = watcher.refresh(force=True)
            self.assertEqual((unreadable.collected_count, unreadable.uncollected_count,
                              unreadable.unknown_count), (1, 107, 0))
            self.assertIn("Matching SKSE co-save: unreadable", unreadable.warnings)


class PublicApiTests(unittest.TestCase):
    def test_startup_does_not_build_map_and_reports_missing_cache(self):
        catalog = synthetic_catalog()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saves = root / "saves"
            saves.mkdir()
            catalog_path = root / "effective-shards.json"
            catalog_text = json.dumps([dict(
                editor_id=s.id, loaded_form_id=s.loaded_form_id,
                loaded_local_form_id=f"{s.loaded_local_id:06X}",
                worldspace="Tamriel", cell="00000D74", x=0, y=0, z=0,
                coordinate_source="synthetic") for s in catalog.shards])
            catalog_path.write_text(catalog_text, encoding="utf-8")
            (root / "identity-validation.json").write_text(json.dumps(dict(
                validation_kind=VALIDATOR_VERSION, loaded_sha256="0" * 64,
                loaded_esl_flag=True, plugin_kind="light", plugin_masters=["Skyrim.esm"],
                self_index=1, shard_count=108, absorbed_list_form_id="0100080A",
                catalog_sha256=hashlib.sha256(catalog_text.encode("utf-8")).hexdigest(),
                counter_global_form_ids={name: f"0100{value:04X}"
                    for name, value in catalog.counter_local_ids.items()})), encoding="utf-8")
            config = TrackerConfig(saves, catalog_path, 15, "127.0.0.1", 8000,
                                   map_cache_directory=root / "map")
            with patch("skyshard_tracker.mapgen.build_map", side_effect=AssertionError("unexpected build")):
                with TestClient(create_app(config, stability_delay=0.01)) as client:
                    self.assertEqual(client.get("/openapi.json").json()["info"]["version"],
                                     package_version("skyshard-atlas"))
                    self.assertEqual(client.get("/api/map").json()["state"], "error")
                    self.assertEqual(client.get("/api/progress").json()["unknown_count"], 108)
                    self.assertFalse(client.get("/api/health").json()["map_available"])
                    self.assertEqual(client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
