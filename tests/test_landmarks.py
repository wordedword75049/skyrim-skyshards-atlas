from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from skyshard_tracker.landmarks import SITES, build_landmarks
from skyshard_tracker.projection import project_world


GAME = Path(os.environ["SKYRIM_GAME_DIR"]) if os.environ.get("SKYRIM_GAME_DIR") else None


class LandmarkTests(unittest.TestCase):
    def test_curated_sites_are_unique(self):
        self.assertEqual(len(SITES), 24)
        self.assertEqual(len(set(SITES)), 24)
        self.assertIn("Whiterun", {name for name, _ in SITES.values()})

    @unittest.skipUnless(GAME is not None and (GAME / "Data/Skyrim.esm").is_file(),
                         "Set SKYRIM_GAME_DIR for vanilla plugin integration")
    def test_vanilla_landmarks_have_traceable_world_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "landmarks.json"
            payload = build_landmarks(GAME, output)
            modified = output.stat().st_mtime_ns
            self.assertEqual(build_landmarks(GAME, output), payload)
            self.assertEqual(output.stat().st_mtime_ns, modified)
            self.assertEqual(build_landmarks(GAME, output, force=True), payload)
        self.assertEqual(len(payload["features"]), 24)
        self.assertEqual({row["winning_plugin"] for row in payload["features"]}, {"Skyrim.esm"})
        self.assertTrue(all(0 <= project_world(row["x"], row["y"])[0] <= 8192 and
                            0 <= project_world(row["x"], row["y"])[1] <= 8192
                            for row in payload["features"]))


if __name__ == "__main__":
    unittest.main()
