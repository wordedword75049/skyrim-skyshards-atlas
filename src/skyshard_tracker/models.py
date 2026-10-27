from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Coordinates(FrozenModel):
    x: float
    y: float
    z: float


class ShardDefinition(FrozenModel):
    id: str
    hold: str
    worldspace: str
    cell: str
    coordinates: Coordinates
    loaded_local_id: int
    loaded_form_id: str
    original_form_id: str | None
    location_hint: str | None
    coordinate_source: str


@dataclass(frozen=True, slots=True)
class Catalog:
    shards: tuple[ShardDefinition, ...]
    by_id: Mapping[str, ShardDefinition]
    by_local_id: Mapping[int, ShardDefinition]
    version: str
    loaded_plugin_sha256: str
    absorbed_list_local_id: int
    counter_local_ids: Mapping[str, int]
    plugin_kind: Literal["full", "light"] = "light"


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    save_directory: Path
    catalog_path: Path
    poll_interval_seconds: int
    host: str
    port: int
    map_cache_directory: Path | None = None
    game_directory: Path | None = None


class SaveIdentity(FrozenModel):
    filename: str
    character: str | None
    save_number: int
    modified_at: datetime
    parsed_at: datetime
    co_save_status: Literal["stable", "missing", "pending", "unreadable"]


class PlayerLocation(FrozenModel):
    status: Literal["available", "unavailable"]
    reason: str | None
    saved_location: str | None
    current_cell_form_id: str | None
    worldspace: Literal["Tamriel"] | None
    is_exterior: bool | None
    x: float | None
    y: float | None
    z: float | None
    rotation: float | None
    evidence: tuple[str, ...]


class ShardProgress(FrozenModel):
    id: str
    hold: str
    worldspace: str
    cell: str
    coordinates: Coordinates
    status: Literal["collected", "uncollected", "unknown"]
    save_form_id: str | None
    confidence: Literal["validated", "positive", "insufficient"]
    evidence: tuple[str, ...]


class ProgressSnapshot(FrozenModel):
    catalog_version: str
    total: int
    collected_count: int
    uncollected_count: int
    unknown_count: int
    shards: tuple[ShardProgress, ...]
    save: SaveIdentity | None
    player_location: PlayerLocation
    is_newest: bool
    newest_candidate: str | None
    newer_save_error: str | None
    warnings: tuple[str, ...]
    last_successful_refresh_at: datetime | None
