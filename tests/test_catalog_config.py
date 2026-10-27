from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skyshard_tracker.catalog import CatalogError, load_catalog
from skyshard_tracker.catalog_import import ensure_catalog, export_catalog
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
                self.assertEqual(config.game_directory, root / "game-a")
                self.assertEqual(config.save_directory, root / "saves-a")
            with patch.dict(os.environ, {"SKYRIM_GAME_DIR": str(root / "game-b"),
                                      "SKYRIM_SAVES_DIR": str(root / "saves-b")}, clear=True):
                config = load_config(env_file)
                self.assertEqual(config.game_directory, root / "game-b")
                self.assertEqual(config.save_directory, root / "saves-b")
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


if __name__ == "__main__":
    unittest.main()
