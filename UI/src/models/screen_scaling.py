"""Shared 1920x1080-base scaling used by the sorter and the market lister."""
from dataclasses import dataclass

BASE_RESOLUTION = (1920, 1080)
STANDARD_ASPECT = 16.0 / 9.0


@dataclass(frozen=True)
class Scale:
    sx: float
    sy: float
    offset_x: float


def scale_for(resolution) -> Scale:
    w, h = resolution
    if (w / max(1, h)) > (STANDARD_ASPECT + 0.01):
        s = h / BASE_RESOLUTION[1]
        return Scale(s, s, (w - h * STANDARD_ASPECT) / 2.0)
    return Scale(w / BASE_RESOLUTION[0], h / BASE_RESOLUTION[1], 0.0)


def scale_point(x, y, scale: Scale):
    return int(round(x * scale.sx + scale.offset_x)), int(round(y * scale.sy))


def scale_length(value, scale: Scale) -> float:
    return max(value * scale.sy, 1.0)
