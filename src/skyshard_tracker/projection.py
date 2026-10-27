"""Single authoritative Skyrim world-to-map transform."""

WORLD_MIN = -262144
WORLD_MAX = 262144
WORLD_SPAN = WORLD_MAX - WORLD_MIN
OVERVIEW_SIZE = 8192
DETAIL_SIZE = 16384


def project_world(x: float, y: float, image_size: int = OVERVIEW_SIZE) -> tuple[float, float]:
    scale = image_size / WORLD_SPAN
    return (x - WORLD_MIN) * scale, (WORLD_MAX - y) * scale


def projection(image_size: int = OVERVIEW_SIZE) -> tuple[float, float, float, float, float, float]:
    scale = image_size / WORLD_SPAN
    return scale, 0.0, -WORLD_MIN * scale, 0.0, -scale, WORLD_MAX * scale
