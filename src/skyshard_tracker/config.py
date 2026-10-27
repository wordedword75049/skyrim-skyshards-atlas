"""Two-path local configuration. System variables override project .env values."""

from __future__ import annotations

import os
from pathlib import Path

from .models import TrackerConfig


class ConfigError(ValueError):
    """Required Skyrim paths are missing or invalid."""


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_NAMES = ("SKYRIM_GAME_DIR", "SKYRIM_SAVES_DIR")


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            if not separator or key.strip() not in ENV_NAMES:
                raise ConfigError(f"{path}:{number}: expected SKYRIM_GAME_DIR or SKYRIM_SAVES_DIR")
            value = value.strip().strip('"').strip("'")
            if not value:
                raise ConfigError(f"{path}:{number}: {key} is empty")
            values[key.strip()] = value
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    return values


def load_config(path: Path | str | None = None) -> TrackerConfig:
    """Resolve two user-facing paths; internal output paths stay in ignored cache."""
    env_file = Path(path) if path is not None else PROJECT_ROOT / ".env"
    file_values = _dotenv(env_file)
    values = {key: os.environ.get(key, file_values.get(key)) for key in ENV_NAMES}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ConfigError(f"Set {', '.join(missing)} in the environment or {env_file}; see .env.example")
    def resolved(value: str) -> Path:
        candidate = Path(value).expanduser()
        return (candidate if candidate.is_absolute() else env_file.parent / candidate).resolve()

    game_dir = resolved(values["SKYRIM_GAME_DIR"])
    save_dir = resolved(values["SKYRIM_SAVES_DIR"])
    if not (game_dir / "Data").is_dir():
        raise ConfigError(f"SKYRIM_GAME_DIR must contain a Data folder: {game_dir}")
    if not save_dir.is_dir():
        raise ConfigError(f"SKYRIM_SAVES_DIR is missing or inaccessible: {save_dir}")
    return TrackerConfig(save_dir, PROJECT_ROOT / "assets/local/catalog/effective-shards.json",
                         15, "127.0.0.1", 8000,
                         game_directory=game_dir,
                         map_cache_directory=PROJECT_ROOT / "assets/local/map/vanilla")
