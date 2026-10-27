"""Loopback FastAPI transport for read-only save progress and one local terrain map."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .catalog import load_catalog
from .enhanced_terrain import EnhancedTerrainAssets, TerrainGeometryError
from .mapgen import MapBuildError, load_active_map
from .models import ProgressSnapshot, TrackerConfig
from .projection import project_world
from .watcher import SaveWatcher


LOG = logging.getLogger(__name__)


class NoStoreStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


class Health(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    configuration_valid: bool
    save_directory_accessible: bool
    catalog_available: bool
    map_available: bool
    watcher_running: bool
    last_successful_update: datetime | None
    save_directory: str
    current_error: str | None


def create_app(config: TrackerConfig, *, stability_delay: float = 1.0,
               enhanced_assets: EnhancedTerrainAssets | None = None) -> FastAPI:
    catalog = load_catalog(config.catalog_path)
    watcher = SaveWatcher(config.save_directory, catalog, stability_delay=stability_delay)
    map_error = None
    if enhanced_assets is None:
        try:
            enhanced_assets = load_active_map(config.map_cache_directory or Path("assets/local/map/vanilla"))
        except MapBuildError as exc:
            map_error = str(exc)
    LOG.info("Loaded catalog %s with %d Skyshards", catalog.version[:12], len(catalog.shards))
    LOG.info("Configured save directory: %s", config.save_directory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        watcher.running = True
        LOG.info("Save watcher active; poll interval %d seconds", config.poll_interval_seconds)
        await asyncio.to_thread(watcher.refresh)

        async def poll():
            while True:
                await asyncio.sleep(config.poll_interval_seconds)
                try:
                    await asyncio.to_thread(watcher.refresh)
                except Exception:
                    LOG.exception("Unexpected watcher failure")

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            watcher.running = False
            LOG.info("Save watcher stopped")

    app = FastAPI(title="Skyshard Atlas", version="0.2.0", lifespan=lifespan)
    app.state.watcher = watcher
    app.state.enhanced_assets = enhanced_assets
    static_root = Path(__file__).with_name("static")
    app.mount("/static", NoStoreStaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_root / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/map")
    def get_map() -> dict:
        if enhanced_assets is None or enhanced_assets.metadata is None:
            return {"state": "error", "error": map_error or "Run 'uv run skyshard-tracker build-map'"}
        metadata = enhanced_assets.metadata
        width, height = metadata["output_dimensions"]
        markers = [dict(id=shard.id, pixel_x=project_world(shard.coordinates.x,
                         shard.coordinates.y, width)[0], pixel_y=project_world(
                         shard.coordinates.x, shard.coordinates.y, height)[1])
                   for shard in catalog.shards]
        scale = width / 8192
        return {"state": "ready", "basemap": "enhanced", "image_url": metadata["image_url"],
                "width": width, "height": height, "markers": markers,
                "view_bbox": [700 * scale, 1300 * scale, 7500 * scale, 6750 * scale],
                "projection": metadata["settings"]["projection"],
                "world_units_per_pixel": metadata["world_units_per_pixel"],
                "detail_world_units_per_pixel": metadata["detail_world_units_per_pixel"],
                "water_url": metadata["water_url"],
                "terrain_tile_url": metadata["terrain_tile_url"],
                "water_tile_url": metadata["water_tile_url"],
                "attribution": metadata["attribution"]}

    @app.get("/api/landmarks")
    def get_landmarks() -> dict:
        path = config.catalog_path.parent.parent / "landmarks.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if len(payload["features"]) != 24:
                raise ValueError("Expected 24 vanilla landmarks")
            return payload
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=503, detail=f"Local landmark catalog unavailable: {exc}") from exc

    @app.get("/map/enhanced/{layer}-overview.webp", include_in_schema=False)
    def enhanced_overview(layer: Literal["terrain", "water"]) -> FileResponse:
        if enhanced_assets is None:
            raise HTTPException(status_code=503, detail="Run build-map first")
        path = enhanced_assets.terrain_overview if layer == "terrain" else enhanced_assets.water_overview
        if not path.is_file():
            raise HTTPException(status_code=503, detail="Map overview unavailable")
        return FileResponse(path, media_type="image/webp")

    @app.get("/map/enhanced/{layer}/{x}/{y}.webp", include_in_schema=False)
    def enhanced_tile(layer: Literal["terrain", "water"], x: int, y: int) -> FileResponse:
        if enhanced_assets is None or not (0 <= x < 32 and 0 <= y < 32):
            raise HTTPException(status_code=404, detail="Map tile unavailable")
        try:
            path = enhanced_assets.tile_path(layer, x, y)
        except TerrainGeometryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=503, detail="Map tile unavailable")
        return FileResponse(path, media_type="image/webp")

    @app.get("/api/progress", response_model=ProgressSnapshot)
    def progress() -> ProgressSnapshot:
        return watcher.snapshot

    @app.post("/api/refresh", response_model=ProgressSnapshot)
    async def refresh() -> ProgressSnapshot:
        return await asyncio.to_thread(watcher.refresh, force=True)

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        snapshot = watcher.snapshot
        try:
            accessible = config.save_directory.is_dir()
            if accessible:
                next(config.save_directory.iterdir(), None)
        except OSError:
            accessible = False
        available = enhanced_assets is not None and enhanced_assets.metadata is not None
        return Health(status="ok" if accessible and available and snapshot.newer_save_error is None else "degraded",
                      configuration_valid=True, save_directory_accessible=accessible,
                      catalog_available=True, map_available=available, watcher_running=watcher.running,
                      last_successful_update=snapshot.last_successful_refresh_at,
                      save_directory=str(config.save_directory),
                      current_error=snapshot.newer_save_error or map_error)

    return app
