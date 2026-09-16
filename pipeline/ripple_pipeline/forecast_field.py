"""Regional loading-anomaly field for the forecast frames.

What this is: forecast rainfall from Open-Meteo over a coarse sample grid, run through the
same SCS curve-number response the point forecast uses, expressed as a *relative* loading
anomaly on top of the latest observed composite.

What this is not: a routed hydrological model. There is no catchment delineation, no
reservoir operation, no travel time, and no calibrated yield. It answers "where is new
sediment and nutrient loading likely this week, relative to the rest of the region" -- it
must never be read as a concentration forecast. The label in the manifest says so, and the
app shows it on every forecast frame.

The anomaly is additive on the observed risk so the animation shows the *change* the
weather is expected to bring; the observed spatial pattern is preserved rather than
replaced by a rainfall map wearing the water-quality palette.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import numpy as np

from .config import OPEN_METEO_FORECAST
from .runoff import EROSION_FACTORS, cn_from_antecedent_rainfall, runoff_from_cn

_TIMEOUT_S = 60
_CHUNK = 96
ANOMALY_DIVISOR = 25.0


def box_blur(values: np.ndarray, k: int = 7) -> np.ndarray:
    """Separable box blur with edge padding. ``k`` odd; a no-op for k <= 1."""
    if k <= 1:
        return values
    if k % 2 == 0:
        k += 1
    pad = k // 2
    padded = np.pad(values, pad, mode="edge")
    cumsum = np.cumsum(padded, axis=0, dtype="float64")
    cumsum = np.concatenate([np.zeros((1, cumsum.shape[1])), cumsum], axis=0)
    out = (cumsum[k:, :] - cumsum[:-k, :]) / k
    cumsum = np.cumsum(out, axis=1, dtype="float64")
    cumsum = np.concatenate([np.zeros((cumsum.shape[0], 1)), cumsum], axis=1)
    out = (cumsum[:, k:] - cumsum[:, :-k]) / k
    return out.astype(values.dtype)


def interp_field(coarse: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Bilinear interpolation of a (n_lat, n_lon) field onto an (out_h, out_w) grid.

    Row 0 of the input is the northern edge, matching raster order.
    """
    n_lat, n_lon = coarse.shape
    src_rows = np.linspace(0.0, 1.0, n_lat)
    src_cols = np.linspace(0.0, 1.0, n_lon)
    dst_rows = np.linspace(0.0, 1.0, out_h)
    dst_cols = np.linspace(0.0, 1.0, out_w)

    along_lon = np.empty((n_lat, out_w), dtype="float64")
    for i in range(n_lat):
        along_lon[i] = np.interp(dst_cols, src_cols, coarse[i])

    out = np.empty((out_h, out_w), dtype="float64")
    for j in range(out_w):
        out[:, j] = np.interp(dst_rows, src_rows, along_lon[:, j])
    return out


def upsample_repeat(coarse: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Nearest-neighbour block upscale, cropped to exactly (out_h, out_w)."""
    fy = max(1, int(np.ceil(out_h / coarse.shape[0])))
    fx = max(1, int(np.ceil(out_w / coarse.shape[1])))
    big = np.kron(coarse, np.ones((fy, fx), dtype=coarse.dtype))
    return big[:out_h, :out_w]


def loading_anomaly(rain_mm, antecedent_mm, land_cover: str = "row_crop",
                    soil_group: str = "B", divisor: float = ANOMALY_DIVISOR):
    """Relative 0..1 loading anomaly from rainfall, via SCS-CN and an erosion factor."""
    cn = cn_from_antecedent_rainfall(land_cover, soil_group, antecedent_mm)
    runoff = runoff_from_cn(rain_mm, cn)
    load = runoff * EROSION_FACTORS[land_cover]
    return np.clip(load / float(divisor), 0.0, 1.0)


def fetch_rainfall_field(lon_min: float, lat_min: float, lon_max: float, lat_max: float,
                         n_lon: int = 12, n_lat: int = 8, days: int = 7,
                         past_days: int = 5) -> dict:
    """Daily rainfall and 5-day antecedent rainfall on a coarse lon/lat sample grid.

    Returns ``{"dates": [...], "antecedent": (n_lat, n_lon), "rain": [day][n_lat, n_lon]}``
    with row 0 at the northern edge.
    """
    lons = np.linspace(lon_min, lon_max, n_lon)
    lats = np.linspace(lat_max, lat_min, n_lat)  # north -> south, raster order
    grid_lons, grid_lats = np.meshgrid(lons, lats)

    flat_lat = grid_lats.ravel()
    flat_lon = grid_lons.ravel()
    rain_days: list[np.ndarray] = []
    dates: list[str] = []
    antecedent = np.zeros(flat_lat.shape, dtype="float64")

    for start in range(0, flat_lat.size, _CHUNK):
        chunk_lat = flat_lat[start:start + _CHUNK]
        chunk_lon = flat_lon[start:start + _CHUNK]
        params = {
            "latitude": ",".join(f"{v:.4f}" for v in chunk_lat),
            "longitude": ",".join(f"{v:.4f}" for v in chunk_lon),
            "daily": "precipitation_sum",
            "forecast_days": str(int(days)),
            "past_days": str(int(past_days)),
            "timezone": "UTC",
        }
        url = f"{OPEN_METEO_FORECAST}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            payload = json.loads(resp.read())
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != chunk_lat.size:
            raise RuntimeError(
                f"open-meteo returned {len(locations)} locations for {chunk_lat.size}")

        for index, loc in enumerate(locations):
            _check_location(loc, chunk_lat[index], chunk_lon[index])
            daily = loc["daily"]
            series = [float(v or 0.0) for v in daily["precipitation_sum"]]
            if not dates:
                dates = list(daily["time"])[past_days:]
            antecedent[start + index] = float(np.sum(series[:past_days]))
            if not rain_days:
                rain_days = [[] for _ in range(days)]
            for day in range(days):
                rain_days[day].append(series[past_days + day])

    return {
        "dates": dates,
        "antecedent": antecedent.reshape(n_lat, n_lon),
        "rain": [np.asarray(day, dtype="float64").reshape(n_lat, n_lon)
                 for day in rain_days],
    }


def _check_location(loc: dict, lat: float, lon: float) -> None:
    """Open-Meteo snaps to its own grid; a large move means the response ordering is off."""
    if len(loc.get("daily", {}).get("time", [])) == 0:
        raise RuntimeError("open-meteo response is missing daily values")
    if abs(float(loc.get("latitude", lat)) - lat) > 0.75 \
            or abs(float(loc.get("longitude", lon)) - lon) > 0.75:
        raise RuntimeError(
            f"open-meteo relocated {lat},{lon} to {loc.get('latitude')},{loc.get('longitude')}")


def anomaly_fields(rainfall: dict, out_h: int, out_w: int, land_cover: str = "row_crop",
                   soil_group: str = "B", divisor: float = ANOMALY_DIVISOR,
                   smooth_k: int = 7) -> list[np.ndarray]:
    """Per-day anomaly fields, smoothed and upscaled to (out_h, out_w)."""
    antecedent = rainfall["antecedent"]
    fields: list[np.ndarray] = []
    for day in rainfall["rain"]:
        coarse = loading_anomaly(day, antecedent, land_cover, soil_group, divisor)
        smoothed = box_blur(coarse, k=smooth_k)
        fields.append(upsample_repeat(smoothed.astype("float32"), out_h, out_w))
    return fields
