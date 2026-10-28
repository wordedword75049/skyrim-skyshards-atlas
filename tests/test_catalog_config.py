from __future__ import annotations

import os
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skyshard_tracker.catalog import CatalogError, load_catalog
from skyshard_tracker.catalog_import import VALIDATOR_VERSION, ensure_catalog, export_catalog
from skyshard_tracker.config import ConfigError, load_config
from skyshard_tracker.plugin_records import PluginError


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PLUGIN = ROOT / "research/inputs/Skyshards-loaded.esp"


class ConfigTests(unittest.TestCase):
    def test_dotenv_and_system_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("game-a", "game-b"):
                (root / name / "Data").mkdir(parents=True)
            for name in ("saves-a", "saves-b"):
                (root / name).mkdir()
            env_file = root / ".env"
            env_file.write_text(f'SKYRIM_GAME_DIR={root / "game-a"}\nSKYRIM_SAVES_DIR={root / "saves-a"}\n',
                                encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(env_file)
                self.assertTrue(config.game_directory.samefile(root / "game-a"))
                self.assertTrue(config.save_directory.samefile(root / "saves-a"))
            with patch.dict(os.environ, {"SKYRIM_GAME_DIR": str(root / "game-b"),
                                      "SKYRIM_SAVES_DIR": str(root / "saves-b")}, clear=True):
                config = load_config(env_file)
                self.assertTrue(config.game_directory.samefile(root / "game-b"))
                self.assertTrue(config.save_directory.samefile(root / "saves-b"))
                self.assertEqual(config.host, "127.0.0.1")

    def test_missing_and_invalid_paths_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / ".env"
            with self.assertRaisesRegex(ConfigError, "SKYRIM_GAME_DIR"):
                load_config(env_file)
            env_file.write_text("SKYRIM_GAME_DIR=missing\nSKYRIM_SAVES_DIR=missing\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Data folder"):
                load_config(env_file)
            env_file.write_text("EXTRA_PATH=bad\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(env_file)


class CatalogImportTests(unittest.TestCase):
    def test_missing_or_unsupported_plugin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Data").mkdir()
            with self.assertRaisesRegex(PluginError, "import-plugin"):
                ensure_catalog(root, root / "effective-shards.json")
            invalid = root / "Skyshards.esp"
            invalid.write_bytes(b"TES4" + bytes(24))
            with self.assertRaises(PluginError):
                export_catalog(invalid, root / "effective-shards.json")

    @unittest.skipUnless(PRIVATE_PLUGIN.is_file(), "Private validated plugin unavailable")
    def test_validated_plugin_rebuilds_all_108_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "effective-shards.json"
            export_catalog(PRIVATE_PLUGIN, output)
            catalog = load_catalog(output)
            self.assertEqual(len(catalog.shards), 108)
            self.assertEqual(catalog.by_id["DMN_SkyshardFalkreath04"].loaded_local_id, 0x881)
            self.assertEqual(catalog.absorbed_list_local_id, 0x80A)
            output.write_bytes(output.read_bytes() + b" ")
            with self.assertRaisesRegex(CatalogError, "disagree"):
                load_catalog(output)


class CatalogSourceSelectionTests(unittest.TestCase):
    """Public cache-policy tests use synthetic bytes; structural tests use real ESPs."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "game"
        (self.game / "Data").mkdir(parents=True)
        self.data_plugin = self.game / "Data/Skyshards.esp"
        self.manual_plugin = self.root / "MO2/Skyshards.esp"
        self.manual_plugin.parent.mkdir()
        self.output = self.root / "cache/effective-shards.json"
        self.output.parent.mkdir()

    def cached(self, source: Path, payload: bytes = b"manual", *, selection: str | None = None):
        source.write_bytes(payload)
        self.output.write_text("synthetic catalog", encoding="utf-8")
        identity = {
            "validation_kind": VALIDATOR_VERSION,
            "source_path": str(source.resolve()),
            "loaded_sha256": hashlib.sha256(payload).hexdigest().upper(),
            "location_patches": [],
        }
        if selection is not None:
            identity["source_selection"] = selection
        self.output.with_name("identity-validation.json").write_text(json.dumps(identity),
                                                                       encoding="utf-8")

    def test_manual_import_wins_over_different_data_plugin_while_source_exists(self):
        self.cached(self.manual_plugin)
        self.data_plugin.write_bytes(b"other plugin")
        with patch("skyshard_tracker.catalog.load_catalog"), patch(
                "skyshard_tracker.catalog_import.export_catalog") as exporter:
            ensure_catalog(self.game, self.output)
        exporter.assert_not_called()

    def test_missing_manual_source_fails_even_when_data_plugin_exists(self):
        self.cached(self.manual_plugin)
        self.data_plugin.write_bytes(b"other plugin")
        self.manual_plugin.unlink()
        with patch("skyshard_tracker.catalog.load_catalog"), self.assertRaisesRegex(
                PluginError, "source is missing.*import-plugin"):
            ensure_catalog(self.game, self.output)

    def test_changed_manual_source_requires_reimport_and_then_recovers(self):
        self.cached(self.manual_plugin)
        self.manual_plugin.write_bytes(b"changed")
        with patch("skyshard_tracker.catalog.load_catalog"), self.assertRaisesRegex(
                PluginError, "changed.*import-plugin"):
            ensure_catalog(self.game, self.output)
        self.cached(self.manual_plugin, b"changed")
        with patch("skyshard_tracker.catalog.load_catalog"):
            ensure_catalog(self.game, self.output)

    def test_changed_data_plugin_is_reimported(self):
        self.cached(self.data_plugin, b"old")
        self.data_plugin.write_bytes(b"new")
        with patch("skyshard_tracker.catalog.load_catalog"), patch(
                "skyshard_tracker.catalog_import.export_catalog") as exporter:
            ensure_catalog(self.game, self.output)
        exporter.assert_called_once_with(self.data_plugin, self.output, source_selection="data")

    def test_changed_configured_game_reimports_automatic_data_catalog(self):
        previous = self.root / "previous/Data/Skyshards.esp"
        previous.parent.mkdir(parents=True)
        self.cached(previous, b"old", selection="data")
        self.data_plugin.write_bytes(b"new")
        with patch("skyshard_tracker.catalog.load_catalog"), patch(
                "skyshard_tracker.catalog_import.export_catalog") as exporter:
            ensure_catalog(self.game, self.output)
        exporter.assert_called_once_with(self.data_plugin, self.output, source_selection="data")

    def test_legacy_cache_from_other_game_data_requires_explicit_selection(self):
        previous = self.root / "previous/Data/Skyshards.esp"
        previous.parent.mkdir(parents=True)
        self.cached(previous, b"old")
        self.data_plugin.write_bytes(b"new")
        with patch("skyshard_tracker.catalog.load_catalog"), self.assertRaisesRegex(
                PluginError, "another game Data folder"):
            ensure_catalog(self.game, self.output)

    def test_missing_source_and_corrupt_cache_are_never_silent(self):
        self.cached(self.manual_plugin)
        self.manual_plugin.unlink()
        with patch("skyshard_tracker.catalog.load_catalog"), self.assertRaisesRegex(
                PluginError, "source is missing"):
            ensure_catalog(self.game, self.output)
        self.output.with_name("identity-validation.json").write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(PluginError, "identity is unreadable"):
            ensure_catalog(self.game, self.output)
        self.output.with_name("identity-validation.json").unlink()
        with self.assertRaisesRegex(PluginError, "cache is incomplete"):
            ensure_catalog(self.game, self.output)

    def test_corrupted_catalog_requires_reimport_without_switching_sources(self):
        self.cached(self.manual_plugin)
        self.data_plugin.write_bytes(b"different plugin")
        with patch("skyshard_tracker.catalog.load_catalog", side_effect=CatalogError("hash mismatch")), patch(
                "skyshard_tracker.catalog_import.export_catalog") as exporter:
            with self.assertRaisesRegex(PluginError, "catalog is invalid.*import-plugin"):
                ensure_catalog(self.game, self.output)
        exporter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
