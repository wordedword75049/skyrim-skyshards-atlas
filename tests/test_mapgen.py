from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from skyshard_tracker.enhanced_terrain import (TerrainGeometryError, TerrainMesh,
                                               WaterSurface, clip_below_water,
                                               read_lod4_mesh_bytes, render_enhanced_tile,
                                               water_mask)
from skyshard_tracker.mapgen import (MapBuildError, VanillaLodSource, build_map,
                                     load_active_map)
from skyshard_tracker.projection import project_world, projection


GAME = Path(os.environ["SKYRIM_GAME_DIR"]) if os.environ.get("SKYRIM_GAME_DIR") else None


class GeometryTests(unittest.TestCase):
    def test_exact_projection(self):
        self.assertEqual(project_world(-262144, 262144), (0, 0))
        self.assertEqual(project_world(0, 0), (4096, 4096))
        self.assertEqual(project_world(262144, -262144), (8192, 8192))
        self.assertEqual(projection(), (1 / 64, 0, 4096, 0, -1 / 64, 4096))

    def test_water_geometry_clips_against_land_height(self):
        triangle = ((0.0, 0.0, -1.0), (10.0, 0.0, 1.0), (0.0, 10.0, -1.0))
        self.assertIn((5.0, 0.0, 0.0), clip_below_water(triangle, 0))
        water = WaterSurface((((0, 0, 0), (16384, 0, 0), (0, 16384, 0)),
                              ((16384, 0, 0), (16384, 16384, 0), (0, 16384, 0))), 0)
        mesh = TerrainMesh((), (water,))
        self.assertGreater(water_mask(mesh, 64).histogram()[255], 3000)
        diffuse = Image.new("RGB", (256, 256), (70, 90, 65))
        normal = Image.new("RGB", (256, 256), (100, 140, 160))
        terrain, mask = render_enhanced_tile(diffuse, normal, mesh)
        self.assertEqual(terrain.size, (512, 512))
        self.assertEqual(mask.size, (512, 512))

    def test_bad_mesh_rejected(self):
        with self.assertRaises(TerrainGeometryError):
            read_lod4_mesh_bytes(b"invalid")


class CacheTests(unittest.TestCase):
    def test_missing_game_archives_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MapBuildError, "Textures7.bsa"):
                VanillaLodSource(Path(directory))

    def test_missing_or_incomplete_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(MapBuildError):
                load_active_map(root)
            (root / "current.json").write_text(json.dumps({"build": "../../outside", "build_version": 1}),
                                               encoding="utf-8")
            with self.assertRaisesRegex(MapBuildError, "outside"):
                load_active_map(root)

    def test_build_skip_force_and_failed_rebuild_keep_previous_pointer(self):
        class Source:
            def __init__(self, _): pass
            def fingerprint(self): return [{"archive": "synthetic", "size": 1, "sha256": "0" * 64}]
            def extract_to(self, destination): destination.mkdir(parents=True)

        class Assets:
            def __init__(self, texture_directory=None, cache_directory=None,
                         mesh_directory=None, source_identity=None):
                self.cache_directory = cache_directory / "enhanced-terrain-v2"
                self.manifest = self.cache_directory / "manifest.json"
                self.source_identity = source_identity
                self.metadata = None
            def prepare(self):
                self.cache_directory.mkdir(parents=True)
                self.manifest.write_text(json.dumps({"source_identity": self.source_identity}), encoding="utf-8")
            def load_cached(self):
                self.metadata = json.loads(self.manifest.read_text(encoding="utf-8"))
                return self.metadata

        with tempfile.TemporaryDirectory() as directory, \
                patch("skyshard_tracker.mapgen.VanillaLodSource", Source), \
                patch("skyshard_tracker.mapgen.EnhancedTerrainAssets", Assets):
            root = Path(directory)
            _, built = build_map(root, root / "cache")
            self.assertTrue(built)
            pointer = (root / "cache/current.json").read_bytes()
            _, built = build_map(root, root / "cache")
            self.assertFalse(built)
            with patch.object(Assets, "prepare", side_effect=OSError("simulated interruption")):
                with self.assertRaises(MapBuildError):
                    build_map(root, root / "cache", force=True)
            self.assertEqual((root / "cache/current.json").read_bytes(), pointer)
            self.assertFalse(list((root / "cache").glob(".building-*")))
            _, built = build_map(root, root / "cache", force=True)
            self.assertTrue(built)
            self.assertNotEqual((root / "cache/current.json").read_bytes(), pointer)


@unittest.skipUnless(GAME is not None and (GAME / "Data/Skyrim - Textures7.bsa").is_file(),
                     "Set SKYRIM_GAME_DIR for read-only vanilla archive integration tests")
class VanillaArchiveTests(unittest.TestCase):
    def test_complete_vanilla_lod4_source_and_btr_decode(self):
        source = VanillaLodSource(GAME)
        self.assertEqual(len(source.members), 1024)
        archive_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in source.paths]
        mesh = source.meshes.get_file_stream(Path("meshes/terrain/tamriel/tamriel.4.0.0.btr")).getvalue()
        parsed = read_lod4_mesh_bytes(mesh)
        self.assertGreater(len(parsed.land_triangles), 1000)
        self.assertGreater(len(parsed.water_surfaces), 0)
        self.assertEqual(archive_hashes,
                         [hashlib.sha256(path.read_bytes()).hexdigest() for path in source.paths])

    def test_completed_real_build_is_reused(self):
        cache = Path(__file__).resolve().parents[1] / "assets/local/map/vanilla"
        if not (cache / "current.json").is_file():
            self.skipTest("Run build-map once to exercise real-cache reuse")
        if load_active_map(cache).metadata["source_identity"] != VanillaLodSource(GAME).fingerprint():
            self.skipTest("Existing local build belongs to a different game archive set")
        before = (cache / "current.json").read_bytes()
        assets, built = build_map(GAME, cache)
        self.assertFalse(built)
        self.assertGreater(assets.metadata["water_tiles"], 0)
        self.assertLess(assets.metadata["water_tiles"], 1024)
        self.assertEqual((cache / "current.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
