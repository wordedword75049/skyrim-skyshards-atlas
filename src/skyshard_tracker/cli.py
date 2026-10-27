"""Local service and explicit asset-build commands."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .catalog import CatalogError, load_catalog
from .catalog_import import ensure_catalog, export_catalog
from .config import ConfigError, PROJECT_ROOT, load_config
from .landmarks import build_landmarks
from .mapgen import MapBuildError, build_map
from .plugin_records import PluginError


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline, read-only Skyrim Skyshard Atlas")
    commands = parser.add_subparsers(dest="command")
    build = commands.add_parser("build-map", help="Generate vanilla terrain and water tiles once")
    build.add_argument("--force", action="store_true", help="Build a new generation even if cache is current")
    imported = commands.add_parser("import-plugin", help="Validate a Skyshards.esp outside game Data")
    imported.add_argument("plugin", type=Path)
    imported.add_argument("--patch", type=Path, action="append", default=[],
                          help="Optional validated location patch; repeat for each enabled patch")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config()
        if args.command == "import-plugin":
            export_catalog(args.plugin, config.catalog_path, tuple(args.patch))
            catalog = load_catalog(config.catalog_path)
            print(f"Imported validated plugin: {len(catalog.shards)} Skyshards")
            return
        if args.command == "build-map":
            assets, built = build_map(config.game_directory, config.map_cache_directory, force=args.force)
            places = build_landmarks(config.game_directory, PROJECT_ROOT / "assets/local/landmarks.json",
                                     force=args.force)
            print(f"Map {'built' if built else 'already current'}: {assets.metadata['output_dimensions']} "
                  f"overview, 1024 terrain and water tiles, {assets.metadata['water_tiles']} water tiles; "
                  f"{len(places['features'])} vanilla landmarks")
            try:
                ensure_catalog(config.game_directory, config.catalog_path)
            except PluginError as exc:
                print(f"Skyshard catalog still needs a compatible plugin: {exc}", file=sys.stderr)
            return
        ensure_catalog(config.game_directory, config.catalog_path)
        app = create_app(config)
    except (ConfigError, CatalogError, MapBuildError, PluginError) as exc:
        print(f"Skyshard Atlas: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
