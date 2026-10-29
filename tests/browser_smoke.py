"""Optional real Chrome check: uv run --with playwright python -m tests.browser_smoke."""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from skyshard_tracker.api import create_app
from skyshard_tracker.catalog import load_catalog
from skyshard_tracker.catalog_import import export_catalog
from skyshard_tracker.ess import parse_save
from skyshard_tracker.models import TrackerConfig
from skyshard_tracker.progress import classify

ROOT = Path(__file__).resolve().parents[1]
SAVES = ROOT / "research/inputs/saves"
CHROME = Path(os.environ.get("SKYRIM_TEST_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
MAP_CACHE = ROOT / "assets/local/map/vanilla"
CATALOG = ROOT / "assets/local/catalog/effective-shards.json"
ORIGINAL_PLUGIN = ROOT / "research/inputs/Skyshards.esp"
RUSSIAN_AFTER = ROOT / "research/inputs/xtraSaves/after"
LANDMARKS = ROOT / "assets/local/landmarks.json"


def fixture(directory: Path) -> Path:
    saves = list(directory.glob("*.ess"))
    if len(saves) != 1:
        raise ValueError(f"Expected one ignored ESS fixture in {directory}")
    return saves[0]


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(CHROME.is_file() and CATALOG.is_file() and
                     (MAP_CACHE / "current.json").is_file() and (SAVES / "before").is_dir(),
                     "Chrome and private integration inputs are unavailable")
class BrowserSmoke(unittest.TestCase):
    @unittest.skipUnless(ORIGINAL_PLUGIN.is_file() and RUSSIAN_AFTER.is_dir() and LANDMARKS.is_file(),
                         "Original ESP, localized save, or local landmarks are unavailable")
    def test_localized_save_shows_english_tamriel_player_marker_without_skse_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_dir = root / "saves"
            save_dir.mkdir()
            shutil.copyfile(fixture(RUSSIAN_AFTER), save_dir / "localized.ess")
            catalog_path = root / "catalog/effective-shards.json"
            export_catalog(ORIGINAL_PLUGIN, catalog_path)
            shutil.copyfile(LANDMARKS, root / "landmarks.json")
            port = available_port()
            app = create_app(TrackerConfig(save_dir, catalog_path, 0.1, "127.0.0.1", port,
                                           map_cache_directory=MAP_CACHE), stability_delay=0.01)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                   log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.1)
                self.assertTrue(server.started)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True,
                        args=["--disable-background-networking", "--disable-component-update", "--disable-sync"])
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
                        page.wait_for_function("document.querySelector('#count-collected').textContent === '1'", timeout=30000)
                        page.wait_for_function("document.querySelector('.player-marker') !== null", timeout=30000)
                        self.assertIn("Tamriel", page.locator("#player-location-state").inner_text())
                        self.assertNotIn("Скайрим", page.locator("#player-location-state").inner_text())
                        self.assertTrue(page.locator("#save-warning").evaluate(
                            "node => node.classList.contains('hidden')"))
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=5)

    def test_map_progress_and_automatic_save_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            save_dir = Path(directory)
            before = fixture(SAVES / "before")
            after = fixture(SAVES / "after")
            catalog = load_catalog(CATALOG)
            expected_before = classify(parse_save(before, catalog), catalog,
                                       modified_at=datetime.now(timezone.utc))
            expected_after = classify(parse_save(after, catalog), catalog,
                                      modified_at=datetime.now(timezone.utc))
            self.assertNotEqual(expected_before.collected_count, expected_after.collected_count)
            shutil.copyfile(before, save_dir / before.name)
            port = available_port()
            app = create_app(TrackerConfig(save_dir, CATALOG, 0.1, "127.0.0.1", port,
                                           map_cache_directory=MAP_CACHE), stability_delay=0.01)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                   log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            self.assertTrue(server.started)
            base = f"http://127.0.0.1:{port}"
            errors = []
            requests = []
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True,
                        args=["--disable-background-networking", "--disable-component-update", "--disable-sync"])
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: requests.append(request.url))
                    page.goto(base, wait_until="domcontentloaded")
                    page.wait_for_function("document.querySelector('#map-status').classList.contains('hidden')", timeout=30000)
                    page.wait_for_function("count => document.querySelector('#count-collected').textContent === String(count)",
                                           arg=expected_before.collected_count)
                    self.assertEqual(int(page.locator("#count-uncollected").inner_text()),
                                     expected_before.uncollected_count)
                    self.assertEqual(page.locator(".shard-pin.uncollected").count(),
                                     expected_before.uncollected_count)
                    self.assertEqual(page.locator(".player-marker").count(), 1)
                    self.assertGreaterEqual(page.locator(".geography-city").count(), 9)
                    self.assertEqual(page.locator(".leaflet-image-layer").count(), 2)
                    self.assertTrue(page.locator(".leaflet-image-layer").first.evaluate(
                        "image => image.complete && image.naturalWidth === 8192"))
                    self.assertEqual(page.locator("#basemap-select").count(), 0)
                    page.locator("#filter-collected").check()
                    self.assertEqual(page.locator(".shard-pin.collected").count(),
                                     expected_before.collected_count)
                    new_save = save_dir / after.name
                    shutil.copyfile(after, new_save)
                    future = time.time() + 5
                    os.utime(new_save, (future, future))
                    page.wait_for_function("count => document.querySelector('#count-collected').textContent === String(count)",
                                           arg=expected_after.collected_count, timeout=20000)
                    self.assertEqual(int(page.locator("#count-uncollected").inner_text()),
                                     expected_after.uncollected_count)
                    self.assertIn(after.name, page.locator("#save-file").inner_text())
                    self.assertEqual(page.locator(".shard-pin.collected").count(),
                                     expected_after.collected_count)
                    page.screenshot(path=str(ROOT / "assets/local/browser-smoke.png"))
                    self.assertEqual(errors, [])
                    self.assertTrue(all(url.startswith(base) for url in requests), requests)
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
