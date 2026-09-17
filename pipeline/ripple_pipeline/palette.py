"""Traffic-light risk ramp: green = clean, red = polluted.

The reference weather-radar image uses green->yellow->orange for *precipitation
intensity*, where green means light rain. Ripple reuses that visual language for a
different quantity, so the legend must always name the quantity explicitly and say which
end is cleaner. Green must never be read as "safe to drink".

Mirrors ``app/src/domain/palette.ts`` exactly; the two must stay in sync.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

PALETTE_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.00, (34, 197, 94)),     # #22C55E  cleaner
    (0.35, (163, 230, 53)),    # #A3E635
    (0.55, (250, 204, 21)),    # #FACC15
    (0.75, (249, 115, 22)),    # #F97316
    (1.00, (220, 38, 38)),     # #DC2626  more polluted
]

# Overlay opacity for valid pixels, so basemap labels read through.
OVERLAY_ALPHA = 190


def ramp(values: np.ndarray, alpha_valid: np.ndarray) -> np.ndarray:
    """Map 0..1 risk values to an RGBA uint8 image.

    Invalid pixels receive alpha 0. Values outside 0..1 are clamped, and NaN is treated
    as invalid regardless of the mask.
    """
    clipped = np.clip(np.nan_to_num(values, nan=0.0), 0.0, 1.0).astype("float32")
    stops = np.array([stop for stop, _ in PALETTE_STOPS], dtype="float32")
    colours = np.array([colour for _, colour in PALETTE_STOPS], dtype="float32")

    out = np.zeros(clipped.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = np.clip(
            np.interp(clipped, stops, colours[:, channel]), 0, 255).astype(np.uint8)

    opaque = np.asarray(alpha_valid, dtype=bool) & np.isfinite(values)
    out[..., 3] = np.where(opaque, OVERLAY_ALPHA, 0).astype(np.uint8)
    return out


def write_png_tile(path, rgba: np.ndarray) -> Path:
    """Write an RGBA array as a PNG at `path`, creating parent directories.

    GDAL PAM is disabled so no ``.aux.xml`` sidecar is written -- these are static web
    tiles and the sidecar would be dead weight shipped to the browser.
    """
    import rasterio
    from rasterio.env import Env
    from rasterio.transform import Affine

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgba.shape[:2]
    with Env(GDAL_PAM_ENABLED="NO"):
        with rasterio.open(
            path, "w", driver="PNG", height=height, width=width, count=4,
            dtype="uint8", crs=None, transform=Affine.identity(),
        ) as dst:
            for band in range(4):
                dst.write(rgba[..., band], band + 1)

    for sidecar in (path.with_suffix(path.suffix + ".aux.xml"),):
        if sidecar.exists():
            sidecar.unlink()
    return path


def write_webp_tile(path, rgba: np.ndarray) -> Path:
    """Write the same RGBA array as a *lossless* WebP.

    Identical pixels, ~3x smaller than PNG for this kind of mostly-transparent raster: the
    13-frame loop is ~16 MB as PNG and ~5 MB as WebP, which is the difference between a
    radar loop that preloads in a second and one that stutters on a hotel wifi connection.
    Lossless matters here -- the colours *are* the data, and a lossy encoder's ringing
    around hard bloom edges would be indistinguishable from real gradient.
    """
    from PIL import Image

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgba), mode="RGBA").save(
        path, format="WEBP", lossless=True, quality=100, method=6)
    return path


def write_tile(path, rgba: np.ndarray, fmt: str = "png") -> Path:
    """Write an RGBA tile in the requested format. Returns the path written."""
    if fmt == "webp":
        return write_webp_tile(path, rgba)
    return write_png_tile(path, rgba)
