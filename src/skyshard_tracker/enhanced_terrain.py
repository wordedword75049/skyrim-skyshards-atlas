"""Read xLODGen's SSE LOD4 terrain and water geometry for the local map.

This deliberately supports the small, verified BTR subset emitted by the
installed xLODGen. It rejects unexpected layouts instead of guessing at NIF.
All geometry is tile-local in Skyrim world units; callers add the cell origin.
"""

from __future__ import annotations

import struct
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageOps

from .projection import WORLD_MIN, WORLD_MAX, projection


LOG = logging.getLogger(__name__)
Point3 = tuple[float, float, float]
Triangle = tuple[Point3, Point3, Point3]


class TerrainGeometryError(ValueError):
    """A terrain BTR does not match the validated SSE xLODGen layout."""


@dataclass(frozen=True)
class WaterSurface:
    triangles: tuple[Triangle, ...]
    height: float


@dataclass(frozen=True)
class TerrainMesh:
    land_triangles: tuple[Triangle, ...]
    water_surfaces: tuple[WaterSurface, ...]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _nif_blocks(data: bytes) -> tuple[list[str], list[tuple[str, bytes]]]:
    if not data.startswith(b"Gamebryo File Format, Version 20.2.0.7\n"):
        raise TerrainGeometryError("Unsupported terrain BTR NIF signature")
    pos = data.index(b"\n") + 1
    if len(data) < pos + 14 or _u32(data, pos) != 0x14020007 or data[pos + 4] != 1:
        raise TerrainGeometryError("Unsupported terrain BTR NIF version or byte order")
    user, count, stream = struct.unpack_from("<III", data, pos + 5)
    if (user, stream) != (12, 100) or count > 1000:
        raise TerrainGeometryError("Unsupported terrain BTR SSE stream header")
    pos += 17
    for _ in range(3):  # Bethesda export info, creator and process strings
        length = data[pos]
        pos += 1 + length
    type_count = _u16(data, pos)
    pos += 2
    if type_count > 100:
        raise TerrainGeometryError("Terrain BTR has too many block types")
    types = []
    for _ in range(type_count):
        length = _u32(data, pos)
        pos += 4
        if length > 100 or pos + length > len(data):
            raise TerrainGeometryError("Invalid terrain BTR block type")
        types.append(data[pos:pos + length].decode("ascii"))
        pos += length
    indices = struct.unpack_from(f"<{count}H", data, pos)
    pos += count * 2
    sizes = struct.unpack_from(f"<{count}I", data, pos)
    pos += count * 4
    string_count, _ = struct.unpack_from("<II", data, pos)
    pos += 8
    if string_count > 1000:
        raise TerrainGeometryError("Terrain BTR has too many strings")
    strings = []
    for _ in range(string_count):
        length = _u32(data, pos)
        pos += 4
        if length > 1000 or pos + length > len(data):
            raise TerrainGeometryError("Invalid terrain BTR string")
        strings.append(data[pos:pos + length].decode("ascii", errors="replace"))
        pos += length
    group_count = _u32(data, pos)
    pos += 4 + group_count * 4
    blocks = []
    for index, size in zip(indices, sizes):
        if index >= len(types) or pos + size > len(data):
            raise TerrainGeometryError("Terrain BTR block overruns the file")
        blocks.append((types[index], data[pos:pos + size]))
        pos += size
    return strings, blocks


def _shape_geometry(block: bytes) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]]:
    if len(block) < 116:
        raise TerrainGeometryError("Short terrain BTR shape")
    scale = struct.unpack_from("<f", block, 64)[0]
    translation = struct.unpack_from("<3f", block, 16)
    descriptor = struct.unpack_from("<Q", block, 100)[0]
    stride = (descriptor & 0xF) * 4
    triangles = _u16(block, 108)
    vertices = _u16(block, 110)
    data_size = _u32(block, 112)
    if (stride not in (16, 20) or not 0 < scale <= 32 or
            data_size != stride * vertices + 6 * triangles or
            116 + data_size > len(block)):
        raise TerrainGeometryError("Unsupported terrain BTR vertex layout or data size")
    points = tuple(tuple(v * scale + t for v, t in zip(
        struct.unpack_from("<3f", block, 116 + i * stride), translation))
        for i in range(vertices))
    index_start = 116 + vertices * stride
    faces = tuple(struct.unpack_from("<3H", block, index_start + i * 6)
                  for i in range(triangles))
    if any(index >= vertices for face in faces for index in face):
        raise TerrainGeometryError("Terrain BTR triangle has invalid vertex index")
    return points, faces


def read_lod4_mesh(path: Path) -> TerrainMesh:
    """Read land triangles and authored water surfaces from one LOD4 BTR."""
    try:
        return read_lod4_mesh_bytes(path.read_bytes())
    except OSError as exc:
        raise TerrainGeometryError(f"Cannot read terrain BTR {path}: {exc}") from exc
    except TerrainGeometryError as exc:
        raise TerrainGeometryError(f"Invalid terrain BTR {path}: {exc}") from exc


def read_lod4_mesh_bytes(data: bytes) -> TerrainMesh:
    """Decode one BTR without writing an extracted archive member."""
    try:
        strings, blocks = _nif_blocks(data)
        roots = [b for typ, b in blocks if typ == "BSMultiBoundNode"]
        if not roots:
            raise TerrainGeometryError("Terrain BTR has no root node")
        has_water_node = any(strings[_u32(b, 0)] == "WATER" for b in roots)
        land_shapes = [b for typ, b in blocks if typ == "BSTriShape"]
        water_shapes = [b for typ, b in blocks if typ == "BSSubIndexTriShape"]
        if bool(water_shapes) != has_water_node:
            raise TerrainGeometryError("Terrain water shapes disagree with WATER node")
        if len(land_shapes) != 1:
            raise TerrainGeometryError("Expected one terrain land shape")
        land_points, land_faces = _shape_geometry(land_shapes[0])
        land = tuple((land_points[a], land_points[b], land_points[c])
                     for a, b, c in land_faces)
        surfaces = []
        for shape in water_shapes:
            points, faces = _shape_geometry(shape)
            if not points or max(p[2] for p in points) - min(p[2] for p in points) > 1:
                raise TerrainGeometryError("Unexpected sloping or empty LOD4 water surface")
            if any(not 0 <= p[0] <= 16384 or not 0 <= p[1] <= 16384 for p in points):
                raise TerrainGeometryError("LOD4 water surface exceeds tile bounds")
            surfaces.append(WaterSurface(tuple((points[a], points[b], points[c])
                                               for a, b, c in faces), points[0][2]))
        return TerrainMesh(land, tuple(surfaces))
    except (IndexError, KeyError, UnicodeError, struct.error) as exc:
        raise TerrainGeometryError(f"Cannot decode terrain BTR: {exc}") from exc


def clip_below_water(triangle: Triangle, water_height: float) -> tuple[Point3, ...]:
    """Clip one 3D terrain triangle to the portion below a horizontal water plane."""
    result: list[Point3] = []
    previous = triangle[-1]
    for current in triangle:
        previous_inside = previous[2] <= water_height
        current_inside = current[2] <= water_height
        if previous_inside != current_inside:
            t = (water_height - previous[2]) / (current[2] - previous[2])
            result.append(tuple(a + t * (b - a) for a, b in zip(previous, current)))
        if current_inside:
            result.append(current)
        previous = current
    return tuple(result)


def _clip_above_water(triangle: Triangle, water_height: float) -> tuple[Point3, ...]:
    reversed_z = tuple((x, y, -z) for x, y, z in triangle)
    return tuple((x, y, -z) for x, y, z in clip_below_water(reversed_z, -water_height))


def water_mask(mesh: TerrainMesh, size: int = 512) -> Image.Image:
    """Rasterize only authored water cells, clipped against LOD4 land elevations."""
    if size <= 0:
        raise ValueError("water mask size must be positive")
    mask = Image.new("L", (size, size), 0)
    pixel = size / (4 * 4096)
    for surface in mesh.water_surfaces:
        wet = Image.new("L", (size, size), 0)
        wet_draw = ImageDraw.Draw(wet)
        for triangle in surface.triangles:
            wet_draw.polygon([(p[0] * pixel, (16384 - p[1]) * pixel) for p in triangle], fill=255)
        dry = Image.new("L", (size, size), 0)
        dry_draw = ImageDraw.Draw(dry)
        for triangle in mesh.land_triangles:
            if max(p[2] for p in triangle) < surface.height:
                continue
            polygon = _clip_above_water(triangle, surface.height)
            if len(polygon) >= 3:
                dry_draw.polygon([(p[0] * pixel, (16384 - p[1]) * pixel) for p in polygon], fill=255)
        mask = ImageChops.lighter(mask, ImageChops.subtract(wet, dry))
    return mask


def render_enhanced_tile(diffuse: Image.Image, normal: Image.Image,
                         mesh: TerrainMesh) -> tuple[Image.Image, Image.Image]:
    """Return terrain relief and a separate mesh-derived hydrography mask."""
    if (diffuse.size not in ((256, 256), (512, 512), (1024, 1024)) or
            normal.size not in ((256, 256), (512, 512), (1024, 1024))):
        raise TerrainGeometryError("Expected 256/512/1024 LOD4 diffuse and normals")
    color = diffuse.convert("RGB")
    if color.size != (512, 512):
        color = color.resize((512, 512), Image.Resampling.LANCZOS)
    color = ImageEnhance.Color(color).enhance(1.35)
    color = ImageEnhance.Brightness(color).enhance(1.55)
    # LOD normal channels encode east, up, north. Light arrives from northwest.
    normals = normal.convert("RGB")
    if normals.size != (512, 512):
        normals = normals.resize((512, 512), Image.Resampling.LANCZOS)
    red, green, blue = normals.split()
    directional = Image.blend(ImageOps.invert(red), blue, 0.48)
    directional = Image.blend(directional, green, 0.25)
    directional = ImageEnhance.Contrast(directional).enhance(1.65)
    shade = Image.blend(Image.new("L", color.size, 255), directional, 0.55)
    color = ImageChops.multiply(color, Image.merge("RGB", (shade, shade, shade)))
    mask = water_mask(mesh)
    return color, mask


class EnhancedTerrainAssets:
    """Reproducible, locally cached 32-unit/pixel terrain and water tiles."""

    def __init__(self, texture_directory: Path | None = None,
                 cache_directory: Path = Path("assets/local/map"),
                 mesh_directory: Path | None = None,
                 source_identity: list | None = None):
        self.texture_directory = texture_directory
        self.mesh_directory = mesh_directory or (
            texture_directory.parent.parent.parent / "meshes/terrain/tamriel"
            if texture_directory is not None else None)
        self.source_identity = source_identity
        self.cache_directory = cache_directory / "enhanced-terrain-v2"
        self.terrain_overview = self.cache_directory / "terrain-overview.webp"
        self.water_overview = self.cache_directory / "water-overview.webp"
        self.manifest = self.cache_directory / "manifest.json"
        self.metadata: dict | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _source_coordinates():
        for tile_y in range(32):
            for tile_x in range(32):
                yield tile_x, tile_y, -64 + 4 * tile_x, 60 - 4 * tile_y

    def _source_paths(self, cell_x: int, cell_y: int) -> tuple[Path, Path, Path]:
        stem = f"tamriel.4.{cell_x}.{cell_y}"
        filenames = (f"{stem}.dds", f"{stem}_n.dds", f"{stem}.btr")
        if self.texture_directory is None or self.mesh_directory is None:
            raise TerrainGeometryError("Map source is unavailable; run build-map")
        return (self.texture_directory / filenames[0],
                self.texture_directory / filenames[1], self.mesh_directory / filenames[2])

    def _identity(self) -> list[list[int | str]]:
        if self.source_identity is not None:
            return self.source_identity
        identity = []
        for _, _, cell_x, cell_y in self._source_coordinates():
            for path in self._source_paths(cell_x, cell_y):
                try:
                    stat = path.stat()
                except OSError as exc:
                    raise TerrainGeometryError(f"Required LOD4 map source missing: {path}: {exc}") from exc
                identity.append([str(path.resolve()), stat.st_size, stat.st_mtime_ns])
        return identity

    @staticmethod
    def _save_atomic(image: Image.Image, path: Path, **kwargs) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.stem + "-",
                                             suffix=".webp", delete=False) as handle:
                temporary = Path(handle.name)
            image.save(temporary, "WEBP", **kwargs)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def prepare(self) -> dict:
        with self._lock:
            try:
                metadata = self._prepare_locked()
            except (OSError, ValueError, IndexError) as exc:
                self.error = str(exc)
                raise TerrainGeometryError(str(exc)) from exc
            self.metadata = metadata
            self.error = None
            return metadata

    def _prepare_locked(self) -> dict:
        identity = self._identity()
        settings = {"generator_version": 4, "lod_level": 4, "tile_size": 512,
                    "overview_size": 8192, "water_source": "BTR WATER and land triangles",
                    "projection": projection()}
        if self.manifest.is_file() and self.terrain_overview.is_file() and self.water_overview.is_file():
            try:
                cached = json.loads(self.manifest.read_text(encoding="utf-8"))
                if (cached.get("source_identity") == identity and cached.get("settings") == settings
                        and self.terrain_overview.stat().st_size > 0
                        and self.water_overview.stat().st_size > 0
                        and all(self.tile_path(kind, x, y).is_file()
                                for kind in ("terrain", "water")
                                for x in range(32) for y in range(32))):
                    return cached
            except (OSError, ValueError):
                pass
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        (self.cache_directory / "terrain").mkdir(exist_ok=True)
        (self.cache_directory / "water").mkdir(exist_ok=True)
        terrain_overview = Image.new("RGB", (8192, 8192), (28, 38, 37))
        water_overview = Image.new("RGBA", (8192, 8192), (0, 0, 0, 0))
        water_tiles = 0
        diffuse_sizes: dict[str, int] = {}
        normal_sizes: dict[str, int] = {}
        for tile_x, tile_y, cell_x, cell_y in self._source_coordinates():
            diffuse_path, normal_path, mesh_path = self._source_paths(cell_x, cell_y)
            mesh = read_lod4_mesh(mesh_path)
            with Image.open(diffuse_path) as diffuse, Image.open(normal_path) as normal:
                if diffuse.format != "DDS" or normal.format != "DDS":
                    raise TerrainGeometryError(f"Unexpected LOD4 texture format: {diffuse_path}")
                diffuse_sizes[str(diffuse.width)] = diffuse_sizes.get(str(diffuse.width), 0) + 1
                normal_sizes[str(normal.width)] = normal_sizes.get(str(normal.width), 0) + 1
                terrain, mask = render_enhanced_tile(diffuse, normal, mesh)
            alpha = mask.point(lambda value: int(value * 0.9))
            water = Image.new("RGBA", (512, 512), (38, 135, 170, 0))
            water.putalpha(alpha)
            if mask.getbbox():
                water_tiles += 1
            terrain_overview.paste(terrain.resize((256, 256), Image.Resampling.LANCZOS),
                                   (tile_x * 256, tile_y * 256))
            water_overview.paste(water.resize((256, 256), Image.Resampling.LANCZOS),
                                 (tile_x * 256, tile_y * 256))
            self._save_atomic(terrain, self.tile_path("terrain", tile_x, tile_y), quality=83)
            self._save_atomic(water, self.tile_path("water", tile_x, tile_y), quality=85,
                              method=4, exact=True)
            if (tile_x + 1) % 32 == 0 and tile_y % 4 == 3:
                LOG.info("Enhanced terrain prepared: %d/1024 tiles", (tile_y + 1) * 32)
        self._save_atomic(terrain_overview, self.terrain_overview, quality=83)
        self._save_atomic(water_overview, self.water_overview, quality=85, exact=True)
        metadata = {
            "source_identity": identity, "settings": settings,
            "source_dimensions": [16384, 16384], "output_dimensions": [8192, 8192],
            "world_bounds": [WORLD_MIN, WORLD_MIN, WORLD_MAX, WORLD_MAX],
            "world_units_per_pixel": 64,
            "detail_world_units_per_pixel": 32,
            "diffuse_native_world_units_per_pixel": {"512px_tiles": 32, "256px_tiles": 64},
            "image_url": "/map/enhanced/terrain-overview.webp",
            "water_url": "/map/enhanced/water-overview.webp",
            "terrain_tile_url": "/map/enhanced/terrain/{x}/{y}.webp",
            "water_tile_url": "/map/enhanced/water/{x}/{y}.webp",
            "water_tiles": water_tiles,
            "native_diffuse_tile_sizes": diffuse_sizes,
            "native_normal_tile_sizes": normal_sizes,
            "attribution": "Locally generated from Skyrim Special Edition LOD4 assets; Bethesda game data",
        }
        temporary = self.manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(temporary, self.manifest)
        return metadata

    def tile_path(self, kind: str, x: int, y: int) -> Path:
        if kind not in ("terrain", "water") or not (0 <= x < 32 and 0 <= y < 32):
            raise TerrainGeometryError("Invalid enhanced terrain tile coordinate")
        return self.cache_directory / kind / f"{x}-{y}.webp"

    def load_cached(self) -> dict:
        """Load a completed immutable build without touching source assets."""
        try:
            metadata = json.loads(self.manifest.read_text(encoding="utf-8"))
            settings = metadata["settings"]
            if (settings["generator_version"] != 4 or
                    tuple(settings["projection"]) != projection() or
                    metadata["output_dimensions"] != [8192, 8192]):
                raise TerrainGeometryError("Map manifest version or projection is incompatible")
            paths = [self.terrain_overview, self.water_overview]
            paths.extend(self.tile_path(kind, x, y) for kind in ("terrain", "water")
                         for x in range(32) for y in range(32))
            if any(not path.is_file() or path.stat().st_size == 0 for path in paths):
                raise TerrainGeometryError("Map build is incomplete; run build-map --force")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TerrainGeometryError(f"Map build is unavailable or invalid: {exc}") from exc
        self.metadata = metadata
        return metadata
