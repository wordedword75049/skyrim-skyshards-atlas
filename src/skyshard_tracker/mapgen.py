"""Rebuild the world-aligned map from vanilla Skyrim SE archives, read-only."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from sse_bsa import BSAArchive

from .enhanced_terrain import EnhancedTerrainAssets, TerrainGeometryError


LOG = logging.getLogger(__name__)
TEXTURE_ARCHIVE = "Skyrim - Textures7.bsa"
MESH_ARCHIVE = "Skyrim - Meshes1.bsa"
BUILD_VERSION = 1


class MapBuildError(ValueError):
    """The local game assets cannot produce the supported map."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VanillaLodSource:
    """Targeted BSA reader for the 1024 Tamriel LOD4 DDS/BTR triplets."""

    def __init__(self, game_dir: Path):
        self.data_dir = game_dir / "Data"
        paths = (self.data_dir / TEXTURE_ARCHIVE, self.data_dir / MESH_ARCHIVE)
        for path in paths:
            if not path.is_file():
                raise MapBuildError(f"Required vanilla Skyrim SE archive missing: {path}")
        try:
            self.textures = BSAArchive(paths[0])
            self.meshes = BSAArchive(paths[1])
        except (OSError, ValueError) as exc:
            raise MapBuildError(f"Cannot read Skyrim SE terrain archives: {exc}") from exc
        self.paths = paths
        self.members = []
        for y in range(60, -65, -4):
            for x in range(-64, 64, 4):
                stem = f"tamriel.4.{x}.{y}"
                self.members.append((
                    Path("textures/terrain/tamriel") / f"{stem}.dds",
                    Path("textures/terrain/tamriel") / f"{stem}_n.dds",
                    Path("meshes/terrain/tamriel") / f"{stem}.btr",
                ))
        missing = [str(path) for diffuse, normal, mesh in self.members
                   for path, archive in ((diffuse, self.textures), (normal, self.textures),
                                         (mesh, self.meshes)) if path not in archive.files]
        if missing:
            raise MapBuildError(f"Vanilla LOD4 set is incomplete ({len(missing)}/3072 missing); first: {missing[0]}")

    def fingerprint(self) -> list[dict[str, str | int]]:
        return [{"archive": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}
                for path in self.paths]

    def extract_to(self, destination: Path) -> None:
        """Extract only map members into an ignored staging directory."""
        for index, members in enumerate(self.members, 1):
            for path, archive in zip(members, (self.textures, self.textures, self.meshes)):
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.get_file_stream(path).getvalue())
            if index % 128 == 0:
                LOG.info("Extracted %d/1024 vanilla terrain triplets", index)


def _safe_build_parent(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if not candidate.is_relative_to((root / "builds").resolve()) or candidate.parent.parent != root.resolve():
        raise MapBuildError("Map cache pointer is outside the build directory")
    return candidate


def load_active_map(cache_root: Path) -> EnhancedTerrainAssets:
    """Read the committed build pointer; never prepare imagery on server start."""
    try:
        pointer = json.loads((cache_root / "current.json").read_text(encoding="utf-8"))
        if pointer.get("build_version") != BUILD_VERSION:
            raise MapBuildError("Map cache pointer version is incompatible")
        build_parent = _safe_build_parent(cache_root, pointer["build"])
        assets = EnhancedTerrainAssets(cache_directory=build_parent)
        metadata = assets.load_cached()
        if metadata["source_identity"] != pointer["source_identity"]:
            raise MapBuildError("Map pointer and source manifest disagree")
        return assets
    except (OSError, ValueError, KeyError, TypeError, TerrainGeometryError) as exc:
        raise MapBuildError(f"Local map is missing or incomplete; run 'uv run skyshard-tracker build-map': {exc}") from exc


def build_map(game_dir: Path, cache_root: Path, *, force: bool = False) -> tuple[EnhancedTerrainAssets, bool]:
    """Build in staging, then atomically publish one validated map generation."""
    source = VanillaLodSource(game_dir)
    identity = source.fingerprint()
    cache_root.mkdir(parents=True, exist_ok=True)
    try:
        existing = load_active_map(cache_root)
    except MapBuildError:
        existing = None
    if existing is not None and existing.metadata["source_identity"] == identity and not force:
        LOG.info("Existing map matches vanilla archives; skipping generation")
        return existing, False
    key = hashlib.sha256(json.dumps([BUILD_VERSION, identity], sort_keys=True).encode()).hexdigest()[:20]
    build_name = key if not force and not (cache_root / "builds" / key).exists() else f"{key}-{uuid.uuid4().hex[:8]}"
    stage = cache_root / f".building-{uuid.uuid4().hex}"
    stage_source = stage / "source"
    stage_output = stage / "output"
    build_parent = cache_root / "builds" / build_name
    try:
        LOG.info("Building map from %s and %s", TEXTURE_ARCHIVE, MESH_ARCHIVE)
        source.extract_to(stage_source)
        texture_dir = stage_source / "textures/terrain/tamriel"
        mesh_dir = stage_source / "meshes/terrain/tamriel"
        assets = EnhancedTerrainAssets(texture_dir, stage_output, mesh_dir,
                                       source_identity=identity)
        assets.prepare()
        assets.load_cached()
        build_parent.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage_output, build_parent)
        pointer = {"build": f"builds/{build_name}", "source_identity": identity,
                   "build_version": BUILD_VERSION}
        temporary = cache_root / f"current-{uuid.uuid4().hex}.json.tmp"
        try:
            temporary.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
            os.replace(temporary, cache_root / "current.json")
        finally:
            temporary.unlink(missing_ok=True)
        return load_active_map(cache_root), True
    except (OSError, ValueError, TerrainGeometryError) as exc:
        raise MapBuildError(f"Map build failed; previous build remains active: {exc}") from exc
    finally:
        if stage.exists() and stage.resolve().is_relative_to(cache_root.resolve()):
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                LOG.warning("Could not remove ignored map staging directory %s: %s", stage, exc)
