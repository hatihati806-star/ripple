"""Ripple pipeline orchestration.

The v1 region spans roughly 238 Sentinel-2 granules across four UTM zones, so a frame
cannot be built from a single scene. This module mosaics many scenes onto one Web Mercator
grid, which is also the projection MapLibre renders in -- building the grid in EPSG:3857
avoids a latitude-dependent stretch in the browser.

Frames produced:

* ``obs-XX``  rolling observed composites, oldest first; the last one ends at "now".
  They are all normalized against the **latest** composite's p05-p95 range, so the radar
  loop shows change in the water rather than a per-frame rescaling. A per-frame range
  would make every frame span the full colour ramp and hide the actual signal.
* ``fc-NN``   forecast days: Open-Meteo rainfall run through SCS curve number, expressed as
  a relative loading anomaly added to the latest observed water mask. See
  ``forecast_field`` for what it does and does not claim.

Run:  python -m ripple_pipeline.cli --out ../app/public/data
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

from . import config
from .composite import Observation, best_pixel
from .forecast_field import ANOMALY_DIVISOR, anomaly_fields, fetch_rainfall_field
from .indices import (
    ndci,
    ndti,
    normalize_relative,
    percentile_range,
    possible_bloom_mask,
    risk_score,
)
from .mask import scl_usable
from .palette import PALETTE_STOPS, ramp, write_tile
from .runoff import (
    curve_number,
    curve_number_from_rainfall,
    direct_runoff_mm,
    load_risk,
)
from .scaling import to_reflectance, validate_scaling
from .stac import collect_scenes_by_window
from .waters import (body_labels, body_rc, discover_bodies, footprint_statistics,
                     load_ocean_mask, region_edge_water, sample_body,
                     search_radius_cells)

OPTICAL_ASSETS = ("blue", "green", "red", "rededge1")

DISCLAIMER = (
    "Satellite-retrieved optical proxies, not a regulatory measurement. "
    "Not for drinking or swimming decisions. Bloom intensity only -- "
    "no cyanobacteria or toxicity determination."
)

FORECAST_BASIS = (
    "SCS curve-number runoff response to Open-Meteo forecast rainfall, applied as a "
    "relative loading anomaly on the latest observed composite. No catchment routing and "
    "no calibrated yield: a ranking of where new loading is likely, not a concentration."
)

WEB_MERCATOR = "EPSG:3857"
SCHEMA_VERSION = 3


def build_grid(bbox, res_m: float = config.TARGET_RES_M, max_dim: int = 4096) -> dict:
    """Web Mercator grid covering `bbox`, capped so the long edge is <= `max_dim`.

    A single image overlay is used in v1, so the grid is capped to stay inside WebGL
    texture limits. The effective resolution is reported rather than hidden.
    """
    left, bottom, right, top = transform_bounds("EPSG:4326", WEB_MERCATOR, *bbox)
    width = max(1, int(round((right - left) / res_m)))
    height = max(1, int(round((top - bottom) / res_m)))

    scale = max(1.0, max(width, height) / float(max_dim))
    effective_res = res_m * scale
    width = max(1, int(round((right - left) / effective_res)))
    height = max(1, int(round((top - bottom) / effective_res)))

    return {
        "crs": WEB_MERCATOR,
        "transform": Affine(effective_res, 0.0, left, 0.0, -effective_res, top),
        "width": width,
        "height": height,
        "res_m": effective_res,
        "requested_res_m": res_m,
        "merc_bounds": (left, bottom, right, top),
        "bbox": tuple(bbox),
    }


def ocean_search_cells(ocean: np.ndarray, grid: dict, cell_deg: float,
                       threshold: float = 0.99) -> frozenset[tuple[float, float]]:
    """Search-cell origins whose area is essentially all ocean.

    The ocean is excluded from the water mask, so granules found only over open water can
    contribute nothing -- and on the continental grid ~38% of the search grid is ocean.
    Skipping those cells removes pure cost, including the newest-first reads they would
    otherwise consume before any land scene is reached.
    """
    skip: set[tuple[float, float]] = set()
    lon_min, lat_min, lon_max, lat_max = grid["bbox"]
    lat = lat_min
    while lat < lat_max:
        lon = lon_min
        while lon < lon_max:
            cell = (lon, lat, min(lon + cell_deg, lon_max), min(lat + cell_deg, lat_max))
            window = _grid_window(grid, transform_bounds("EPSG:4326", WEB_MERCATOR, *cell))
            if window is not None:
                rows = slice(max(0, window.row_off),
                             min(grid["height"], window.row_off + window.height))
                cols = slice(max(0, window.col_off),
                             min(grid["width"], window.col_off + window.width))
                patch = ocean[rows, cols]
                if patch.size and patch.mean() >= threshold:
                    skip.add((lon, lat))
            lon += cell_deg
        lat += cell_deg
    return frozenset(skip)


def _grid_window(grid: dict, merc_bounds) -> Window | None:
    """Grid pixel window overlapping the given Mercator bounds, or None."""
    left = max(merc_bounds[0], grid["merc_bounds"][0])
    bottom = max(merc_bounds[1], grid["merc_bounds"][1])
    right = min(merc_bounds[2], grid["merc_bounds"][2])
    top = min(merc_bounds[3], grid["merc_bounds"][3])
    if right <= left or top <= bottom:
        return None

    inverse = ~grid["transform"]
    col0, row0 = inverse * (left, top)
    col1, row1 = inverse * (right, bottom)
    width = int(np.ceil(col1 - col0))
    height = int(np.ceil(row1 - row0))
    if width <= 0 or height <= 0:
        return None
    return Window(int(np.floor(col0)), int(np.floor(row0)), width, height)


def read_into_grid(scene: dict, asset: str, grid: dict,
                   sub: int = 1, resampling: str = "nearest") -> np.ndarray:
    """Read one asset over the region and reproject it onto the grid.

    Returns a uint16 array of raw DN, 0 where outside the scene. The source is decimated
    to approximately the grid resolution before warping, so warping cost tracks the
    output size rather than the ~120 M-pixel source tile.

    ``resampling`` matters more than it looks. Reflectance is continuous and is read with
    bilinear averaging, so one noisy 10 m pixel cannot set the value of a 2 km cell. The
    scene classification layer is *categorical*: averaging class codes is meaningless, so
    it is read nearest at ``sub`` times the grid resolution and aggregated by the caller
    into a water fraction per cell.
    """
    href = scene["assets"][asset]["href"]
    out = np.zeros((grid["height"] * sub, grid["width"] * sub), dtype=np.uint16)

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIPLEX="YES"):
        with rasterio.open(f"/vsicurl/{href}") as ds:
            src_merc = transform_bounds(WEB_MERCATOR, ds.crs, *grid["merc_bounds"])
            window = from_bounds(*src_merc, transform=ds.transform)
            if window.width <= 0 or window.height <= 0:
                return out

            decimation = max(1.0, grid["res_m"] / ds.res[0] / sub)
            out_w = max(1, int(round(abs(window.width) / decimation)))
            out_h = max(1, int(round(abs(window.height) / decimation)))
            source = ds.read(1, window=window, out_shape=(out_h, out_w),
                             boundless=True, fill_value=config.NODATA_DN,
                             resampling=Resampling[resampling])
            src_transform = ds.window_transform(window) * Affine.scale(
                abs(window.width) / out_w, abs(window.height) / out_h)

            scene_merc = transform_bounds(ds.crs, WEB_MERCATOR,
                                          *ds.window_bounds(window))
            target = _grid_window(grid, scene_merc)
            if target is None:
                return out

            # Sub-sampled reads keep the same ground window but a finer cell; the target
            # window in grid cells is divided by the same factor so edges still line up.
            sub_target = Window(target.col_off * sub, target.row_off * sub,
                                target.width * sub, target.height * sub)
            sub_transform = grid["transform"] * Affine.scale(1.0 / sub)
            dest = out[sub_target.row_off:sub_target.row_off + sub_target.height,
                       sub_target.col_off:sub_target.col_off + sub_target.width]
            reproject(
                source=source, destination=dest,
                src_transform=src_transform, src_crs=ds.crs, src_nodata=config.NODATA_DN,
                dst_transform=rasterio.windows.transform(sub_target, sub_transform),
                dst_crs=grid["crs"], dst_nodata=config.NODATA_DN,
                resampling=Resampling.nearest,
            )
    return out


# Ceiling for the fraction of the grid a scene must newly fill to count as progress. A
# scene covers ~110x110 km, so its share of the grid shrinks as the grid grows: on the
# 1.85 km continental grid one full scene is 0.03% of the grid, and a fixed 0.02%
# threshold marked almost every real scene as noise and tripped the patience stop after a
# few hundred scenes per window. The threshold scales with the scene's footprint share,
# with this 0.02% value as the ceiling so small grids keep their measured behaviour.
MIN_GAIN = 2e-4
MIN_GAIN_FLOOR = 2e-5
SCENE_FOOTPRINT_M = 110_000.0


def progress_threshold(grid: dict) -> float:
    """Fraction of the grid a scene must newly fill to count as progress (see MIN_GAIN)."""
    grid_m2 = (grid["width"] * grid["res_m"]) * (grid["height"] * grid["res_m"])
    scene_fraction = (SCENE_FOOTPRINT_M ** 2) / max(1.0, grid_m2)
    return float(min(MIN_GAIN, max(MIN_GAIN_FLOOR, 0.25 * scene_fraction)))


def _read_scene(scene: dict, grid: dict, assets, shape, sub: int = 1) -> dict[str, np.ndarray]:
    """Read every asset for one scene.

    Reflectance is averaged down to the grid cell; the class layer is read at ``sub`` times
    the grid resolution and aggregated by the fold. Both choices exist for the same reason:
    at 1.85 km a single nearest-sampled 10 m pixel decides the value of a cell, which is
    noise on a shoreline and a coin flip on a narrow reservoir.
    """
    layers: dict[str, np.ndarray] = {}
    for asset in assets:
        is_class = asset == "scl"
        try:
            layers[asset] = read_into_grid(
                scene, asset, grid,
                sub=sub if is_class else 1,
                resampling="nearest" if is_class else "average",
            )
        except Exception:
            layers[asset] = np.zeros(
                (shape[0] * (sub if is_class else 1),
                 shape[1] * (sub if is_class else 1)), dtype=np.uint16)
    return layers


def aggregate_class(scl: np.ndarray, sub: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell water fraction and majority-usable flag from a sub-sampled SCL layer."""
    height = scl.shape[0] // sub
    width = scl.shape[1] // sub
    cropped = scl[:height * sub, :width * sub]
    water = (cropped == config.SCL_WATER)
    blocks = water.reshape(height, sub, width, sub)
    water_fraction = (blocks.sum(axis=(1, 3), dtype="uint16") / float(sub * sub)).astype("float32")

    usable = scl_usable(cropped) & (cropped > config.NODATA_DN)
    usable_blocks = usable.reshape(height, sub, width, sub)
    usable_fraction = usable_blocks.sum(axis=(1, 3), dtype="uint16")
    majority_usable = usable_fraction >= ((sub * sub) // 2 + 1)
    return water_fraction, majority_usable


def mosaic_windows(window_scenes: list[list[dict]], grid: dict, assets,
                   workers: int = 6, max_scenes: int = 600,
                   patience: int = 30, sub: int = 1, progress=None,
                   min_gain: float | None = None
                   ) -> tuple[list[dict[str, np.ndarray]], list[np.ndarray], list[int]]:
    """Best-pixel mosaics for several time windows in a single pass over the scenes.

    Two properties matter, and both were paid for in measured runtime:

    * Per pixel, the most recent scene that observed it **usably**. Newer scenes win, but
      only where they carry data -- a naive "newest reflectance wins" fold lets a cloudy
      pass erase clear water from days earlier, and the cloud hole then reads as a data
      outage, which is exactly what compositing exists to prevent (rule R5). Usability
      includes the scene classification layer, so reflectance and SCL always come from the
      same acquisition.
    * Each scene is fetched **once**, even though rolling windows overlap: scene i belongs
      to every window whose dates contain it, and folding it into all of them costs CPU
      instead of a second round trip. Reading the overlap twice cost ~35% of total runtime
      before this, and the per-window scene cap starved exactly the regions whose clear
      acquisitions happened to be older.

    Reading stops per window once scenes stop adding pixels, and a global cap bounds the
    pass. Returns the canvases, each window's water-fraction raster (see
    ``aggregate_class``), and the number of scenes each window actually used.
    """
    shape = (grid["height"], grid["width"])
    if min_gain is None:
        min_gain = progress_threshold(grid)
    assignments: dict[str, tuple[dict, list[int]]] = {}
    for index, scenes in enumerate(window_scenes):
        for scene in scenes:
            entry = assignments.get(scene["id"])
            if entry is None:
                assignments[scene["id"]] = (scene, [index])
            elif index not in entry[1]:
                entry[1].append(index)

    ordered = sorted(assignments.values(),
                     key=lambda item: item[0]["properties"]["datetime"], reverse=True)
    canvases = [{asset: np.zeros(shape, dtype="uint16") for asset in assets}
                for _ in window_scenes]
    water_fractions = [np.zeros(shape, dtype="float32") for _ in window_scenes]
    filled = [np.zeros(shape, dtype=bool) for _ in window_scenes]
    state = [{"used": 0, "stale": 0, "previous_fill": 0.0, "done": False}
             for _ in window_scenes]
    reads = {"total": 0}

    def fold(layers: dict[str, np.ndarray], index: int) -> None:
        window = state[index]
        if window["done"]:
            return
        if sub > 1:
            water_fraction, majority_usable = aggregate_class(layers["scl"], sub)
        else:
            single = layers["scl"]
            water_fraction = (single == config.SCL_WATER).astype("float32")
            majority_usable = scl_usable(single) & (single > config.NODATA_DN)

        mask = majority_usable & ~filled[index]
        for asset in assets:
            if asset == "scl":
                continue
            mask &= layers[asset] > config.NODATA_DN
        for asset in assets:
            if asset == "scl":
                continue
            np.copyto(canvases[index][asset], layers[asset], where=mask)
        np.copyto(water_fractions[index], water_fraction, where=mask)
        filled[index] |= mask
        window["used"] += 1
        current_fill = float(filled[index].mean())
        gain = current_fill - window["previous_fill"]
        window["stale"] = window["stale"] + 1 if gain < min_gain else 0
        window["previous_fill"] = current_fill
        if window["stale"] >= patience or window["used"] >= max_scenes:
            window["done"] = True
            filled[index] = None  # release the mask; only the canvases are needed

    def active() -> bool:
        return not all(window["done"] for window in state)

    if workers > 1 and len(ordered) > 1:
        pending = deque()
        remaining = iter(ordered)

        def submit_next() -> bool:
            """Queue the next scene that still has an unfinished window to land in.

            Newest-first order means a window that reaches its cap leaves all of its
            (thousands of) leftover scenes sitting ahead of the oldest scene of any other
            window. Reading them spends the whole budget with nowhere to put the pixels --
            on the continental grid that burned ~1,650 reads and left three of four
            observed frames empty. Scenes with no active window are skipped for free.
            """
            if reads["total"] >= max_scenes * max(1, len(window_scenes)):
                return False
            while True:
                item = next(remaining, None)
                if item is None:
                    return False
                scene, indices = item
                if any(not state[index]["done"] for index in indices):
                    break
            pending.append((indices, pool.submit(_read_scene, scene, grid, assets, shape, sub)))
            reads["total"] += 1
            return True

        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            for _ in range(workers * 2):
                if not submit_next():
                    break
            while pending:
                indices, future = pending.popleft()
                layers = future.result()
                for index in indices:
                    fold(layers, index)
                if progress is not None and reads["total"] % 250 == 0:
                    progress(reads["total"], sum(w["used"] for w in state),
                             sum(w["done"] for w in state), len(window_scenes))
                if not active():
                    for _, other in pending:
                        other.cancel()
                    break
                submit_next()
        finally:
            pool.shutdown(wait=True)
    else:
        for scene, indices in ordered:
            layers = _read_scene(scene, grid, assets, shape, sub)
            for index in indices:
                fold(layers, index)
            if not active():
                break

    if progress is not None:
        summary = ", ".join(f"w{i}: folds={window['used']} stale={window['stale']}"
                            f"{' (capped)' if window['used'] >= max_scenes else ''}"
                            for i, window in enumerate(state))
        print(f"  window states: {summary}", flush=True)

    return canvases, water_fractions, [window["used"] for window in state]


def mosaic_scenes(scenes: list[dict], grid: dict, assets,
                  workers: int = 6, max_scenes: int = 600,
                  patience: int = 30, sub: int = 1
                  ) -> tuple[dict[str, np.ndarray], np.ndarray, int]:
    """Single-window convenience wrapper around :func:`mosaic_windows`."""
    canvases, fractions, used = mosaic_windows(
        [scenes], grid, assets, workers=workers, max_scenes=max_scenes,
        patience=patience, sub=sub)
    return canvases[0], fractions[0], used[0]


def compute_composite(raw: dict[str, np.ndarray], water_fraction: np.ndarray,
                      scenes: list[dict], grid: dict,
                      now: datetime | None = None,
                      ocean: np.ndarray | None = None) -> dict:
    """NDCI / NDTI composites and coverage for one window's mosaic, without normalization.

    Normalization is applied by the caller so every frame in a sequence can share one
    p05-p95 range (see the module docstring). The water mask is the majority-voted water
    fraction from the class layer, not a single sampled class code. Ocean and region-edge
    water are removed: they are water, but they are not lakes, and their measured area is
    an artifact of where the analysis box was drawn.
    """
    now = now or datetime.now(timezone.utc)

    def scene_time(scene: dict) -> datetime:
        return datetime.fromisoformat(scene["properties"]["datetime"].replace("Z", "+00:00"))

    valid = raw["green"] > config.NODATA_DN
    refl = {k: to_reflectance(raw[k]) for k in OPTICAL_ASSETS}

    if not valid.any():
        raise RuntimeError("no valid pixels in mosaic")

    water = valid & (water_fraction >= config.MASK_WATER_FRACTION)
    # Ocean is water but not a lake, and in the mosaic it is fragmented by cloud and
    # nodata gaps, so it cannot be recognised by touching the region border. Exclude the
    # static coastline (Natural Earth) first, then anything that still reaches the border.
    if ocean is not None:
        water &= ~ocean
    edge = region_edge_water(water, grid)
    if edge.any():
        water &= ~edge
    # Scale validation is scoped to water: a region mosaic is mostly vegetation and soil,
    # where blue < red is normal and the water ordering test would wrongly halt it. The
    # ordering statistic is reported, not enforced -- hypersaline and mineral lakes are
    # legitimately red-dominant (see scaling.py).
    scaling = validate_scaling(refl, valid, "region-mosaic", water=water)
    if not water.any():
        raise RuntimeError("no water pixels in mosaic")

    n_ndci = ndci(refl["red"], refl["rededge1"])
    n_ndti = ndti(refl["red"], refl["green"])

    ages = np.array([(now - scene_time(scene)).total_seconds() / 86400.0
                     for scene in scenes], dtype="float64")
    newest = max(scenes, key=scene_time)

    ndci_obs = Observation(values=n_ndci, valid=water & np.isfinite(n_ndci),
                           age_days=float(np.median(ages)))
    ndti_obs = Observation(values=n_ndti, valid=water & np.isfinite(n_ndti),
                           age_days=float(np.median(ages)))
    c_ndci, _, _ = best_pixel([ndci_obs])
    c_ndti, _, _ = best_pixel([ndti_obs])
    water_mask = np.isfinite(c_ndci) | np.isfinite(c_ndti)

    ndci_range = percentile_range(c_ndci)
    ndti_values = c_ndti[np.isfinite(c_ndti)]
    if ndti_values.size:
        ndti_range = (float(np.percentile(ndti_values, 5)),
                      float(np.percentile(ndti_values, 95)))
    else:
        ndti_range = (0.0, 1.0)

    blooms = possible_bloom_mask(c_ndci) & water_mask
    water_count = int(water_mask.sum())
    wf = np.where(water_mask, water_fraction, np.nan).astype("float32")

    real_ages = [a for a in ages if np.isfinite(a)]
    return {
        "ndci": c_ndci,
        "ndti": c_ndti,
        "valid": water_mask,
        "water_fraction": wf,
        "ndci_range": ndci_range,
        "ndti_range": ndti_range,
        "median_age": float(np.median(real_ages)) if real_ages else None,
        "bloom_fraction": (float(blooms.sum()) / water_count) if water_count else 0.0,
        "source_scene": newest["id"],
        "scene_count": None,  # filled in by the caller, which knows how many were read
        "scenes_available": len(scenes),
        "scene_dates": sorted(scene["properties"]["datetime"][:10] for scene in scenes),
        "coverage": {
            "satellite_valid_fraction": float(valid.mean()),
            "water_fraction_of_grid": float(water_mask.mean()),
        },
        "scaling": scaling,
    }


def risk_from(frame: dict, ndci_bounds, ndti_bounds) -> np.ndarray:
    """Composite 0..1 risk for one frame under a fixed normalization range."""
    risk = risk_score(
        normalize_relative(frame["ndci"], *ndci_bounds),
        normalize_relative(frame["ndti"], *ndti_bounds),
    )
    risk[~frame["valid"]] = np.nan
    return risk


def plan_windows(now: datetime, frames: int, step_days: int,
                 window_days: int) -> list[tuple[datetime, datetime]]:
    """Oldest-to-newest scene windows; the newest ends at ``now``."""
    windows: list[tuple[datetime, datetime]] = []
    for index in range(frames - 1, -1, -1):
        end = now - timedelta(days=step_days * index)
        windows.append((end - timedelta(days=window_days), end))
    return windows


def scenes_in_window(scenes: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Scenes acquired within [start_date, end_date] inclusive. Unparseable stamps are
    skipped. Comparison is by acquisition date, matching how the windows are labelled."""
    start_date = start.date()
    end_date = end.date()
    out: list[dict] = []
    for scene in scenes:
        try:
            stamp = datetime.fromisoformat(
                scene["properties"]["datetime"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start_date <= stamp.date() <= end_date:
            out.append(scene)
    return out


def probe_stride(grid: dict, probe_max_dim: int) -> int:
    """Integer decimation factor so the probe raster's long edge is <= probe_max_dim."""
    return max(1, int(np.ceil(max(grid["width"], grid["height"]) / float(probe_max_dim))))


def write_frame_tiles(out_dir, frame_id: str, risk: np.ndarray,
                      grid: dict, probe_max_dim: int,
                      fmt: str = "png") -> tuple[str, str]:
    """Write the full-resolution tile and its probe-resolution sibling."""
    valid = np.isfinite(risk)
    tile_rel = f"tiles/{frame_id}.{fmt}"
    write_tile(Path(out_dir) / tile_rel, ramp(risk, valid), fmt)

    stride = probe_stride(grid, probe_max_dim)
    probe_risk = risk[::stride, ::stride]
    probe_rel = f"tiles/{frame_id}-probe.{fmt}"
    write_tile(Path(out_dir) / probe_rel, ramp(probe_risk, np.isfinite(probe_risk)), fmt)
    return tile_rel, probe_rel


def prune_tiles(out_dir, keep: set[str]) -> int:
    """Delete tiles from a previous run that the new manifest does not reference.

    Without this, renaming a frame (or changing --history-frames) leaves orphaned megabytes
    in public/ that are still shipped by the dev server and the build.
    """
    tiles_dir = Path(out_dir) / "tiles"
    if not tiles_dir.is_dir():
        return 0
    removed = 0
    for path in tiles_dir.iterdir():
        if path.is_file() and path.name not in keep:
            path.unlink()
            removed += 1
    return removed


def sample_bodies(bodies, frame_id: str, risk: np.ndarray, ndci: np.ndarray,
                  ndti: np.ndarray, valid: np.ndarray, water_fraction: np.ndarray,
                  grid: dict, include_indices: bool,
                  labels: np.ndarray | None = None,
                  footprint: np.ndarray | None = None) -> dict[str, dict]:
    """One body's reading on one frame, averaged over the body's own footprint.

    A body used to be represented by the single most-water cell within 12 km of its
    catalog coordinate. That is a *point* sample, and for a 4,861 km2 bay it reported the
    worst 1.85 km pixel in the whole bay as the bay's reading -- which is how a single
    anomalous cell put Georgian Bay at the top of the leaderboard at a saturated 1.00.

    The headline ``risk`` is now the areal mean over the body's footprint; ``risk_peak``
    is retained beside it, because the worst cell is real information and hiding it would
    be its own kind of dishonesty. ``cells`` and ``cell_fraction`` say how much of the
    footprint actually carried a reading, so a body measured on four cells cannot look
    like a body measured on four thousand.

    When no footprint is available (``labels is None``) the old point sample is used and
    reported with ``cells`` 1, so a caller can always tell which happened.
    """
    out: dict[str, dict] = {}
    if labels is not None:
        stats = footprint_statistics(labels, risk, valid, len(bodies))
        index_stats = (footprint_statistics(labels, ndci, valid, len(bodies)),
                       footprint_statistics(labels, ndti, valid, len(bodies))) \
            if include_indices else None
        fraction = np.where(valid, water_fraction, np.nan)
        fraction_stats = footprint_statistics(labels, fraction, valid, len(bodies))
        for position, body in enumerate(bodies):
            stat = stats[position]
            entry = {
                "frame": frame_id,
                "risk": stat["mean"],
                "risk_peak": stat["peak"],
                "risk_std": stat["std"],
                "cells": stat["cells"],
                "cell_fraction": (round(stat["cells"] / footprint[position], 3)
                                  if footprint is not None and footprint[position] > 0
                                  else None),
                "valid": stat["mean"] is not None,
                "water_fraction": fraction_stats[position]["mean"],
            }
            if include_indices:
                entry["ndci"] = index_stats[0][position]["mean"]
                entry["ndti"] = index_stats[1][position]["mean"]
            out[body.id] = entry
        return out

    radius = search_radius_cells(grid["res_m"])
    for body in bodies:
        rc = body_rc(grid, body)
        if rc is None:
            out[body.id] = {"frame": frame_id, "risk": None, "valid": False}
            continue
        values = sample_body(risk, ndci, ndti, valid, body, rc[0], rc[1],
                             radius_cells=radius, water_fraction=water_fraction)
        entry = {"frame": frame_id, "risk": values["risk"], "valid": values["valid"],
                 "offset_cells": values["offset_cells"],
                 "water_fraction": values["water_fraction"],
                 "risk_peak": values["risk"], "cells": 1 if values["valid"] else 0,
                 "cell_fraction": None}
        if include_indices:
            entry["ndci"] = values["ndci"]
            entry["ndti"] = values["ndti"]
        out[body.id] = entry
    return out


def body_forecast_series(baseline_risk: float, rain_mm: list[float],
                         antecedent_mm: float, land_cover: str = "row_crop",
                         soil_group: str = "B") -> dict:
    """Runoff-driven forecast for one body from its *own* rainfall.

    The point values are Open-Meteo daily totals at the body's coordinates, and the model is
    the same SCS curve number response the frames use. Everything reported here is an input
    or a direct product of that computation -- no filled-in numbers. ``baseline_risk`` is the
    body's last real observation, so the forecast is an increment on a measurement.
    """
    cn = curve_number_from_rainfall(land_cover, soil_group, antecedent_mm)
    runoff = [direct_runoff_mm(mm, cn) for mm in rain_mm]
    anomaly = [min(1.0, load_risk(value, land_cover) / ANOMALY_DIVISOR) for value in runoff]
    return {
        "baseline_risk": round(float(baseline_risk), 4),
        "antecedent_rain_mm": round(float(antecedent_mm), 2),
        "curve_number": round(float(cn), 1),
        "land_cover": land_cover,
        "soil_group": soil_group,
        "rain_mm": [round(float(mm), 2) for mm in rain_mm],
        "runoff_mm": [round(float(value), 2) for value in runoff],
        "risk": [round(min(1.0, baseline_risk + delta), 4) for delta in anomaly],
    }


def forecast_runoff_risk(lat: float, lon: float, land_cover: str, soil_group: str,
                         baseline_risk: float, days: int = 7) -> list[dict]:
    """Runoff-driven risk for one point over the forecast horizon."""
    from .weather import daily_rainfall, fetch_forecast, mean_antecedent_moisture

    point = fetch_forecast(lat, lon, days=days)
    moisture = mean_antecedent_moisture(point)
    cn = curve_number(land_cover, soil_group, moisture)

    out: list[dict] = []
    for day, rain_mm in daily_rainfall(point):
        runoff = direct_runoff_mm(rain_mm, cn)
        anomaly = min(1.0, load_risk(runoff, land_cover) / ANOMALY_DIVISOR)
        out.append({
            "date": day,
            "rain_mm": round(rain_mm, 2),
            "runoff_mm": round(runoff, 2),
            "curve_number": round(cn, 1),
            "risk": round(min(1.0, baseline_risk + anomaly), 4),
        })
    return out[-days:]


def write_manifest(out_dir, manifest: dict) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _daily_rainfall_sample(rainfall: dict, grid: dict, col: int, row: int) -> list[float]:
    """Forecast rainfall (mm/day) at one grid cell, from the coarse rainfall field."""
    i, j = _coarse_cell(rainfall, grid, col, row)
    return [round(float(day[i, j]), 2) for day in rainfall["rain"]]


def _antecedent_rainfall_sample(rainfall: dict, grid: dict, col: int, row: int) -> float:
    """Antecedent rainfall (mm) at one grid cell -- the AMC input to the curve number."""
    i, j = _coarse_cell(rainfall, grid, col, row)
    return round(float(rainfall["antecedent"][i, j]), 2)


def _coarse_cell(rainfall: dict, grid: dict, col: int, row: int) -> tuple[int, int]:
    """Map a grid cell to the nearest cell of the coarse rainfall sample grid."""
    n_lat, n_lon = rainfall["antecedent"].shape
    i = int(np.clip(round(row / max(1, grid["height"] - 1) * (n_lat - 1)), 0, n_lat - 1))
    j = int(np.clip(round(col / max(1, grid["width"] - 1) * (n_lon - 1)), 0, n_lon - 1))
    return i, j


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ripple-pipeline")
    parser.add_argument("--out", default="../app/public/data")
    parser.add_argument("--bbox", nargs=4, type=float, default=list(config.REGION_BBOX))
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--max-dim", type=int, default=4096)
    parser.add_argument("--cell-deg", type=float, default=1.5,
                        help="search cell size for region-wide scene collection")
    parser.add_argument("--per-cell", type=int, default=12,
                        help="granule candidates per search cell per window")
    parser.add_argument("--max-cloud", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=6,
                        help="parallel remote COG reads")
    parser.add_argument("--max-scenes-per-frame", type=int, default=600,
                        help="hard cap on scenes folded into one composite")
    parser.add_argument("--saturation-patience", type=int, default=30,
                        help="stop compositing a window after this many scenes add "
                             "less than MIN_GAIN of new pixels")
    parser.add_argument("--history-frames", type=int, default=4,
                        help="rolling observed composites, oldest first")
    parser.add_argument("--frame-step-days", type=int, default=6)
    parser.add_argument("--frame-window-days", type=int, default=12,
                        help="rolling composite window; wide enough to outlast a cloudy spell")
    parser.add_argument("--probe-max-dim", type=int, default=1024,
                        help="long edge of the decimated probe rasters")
    parser.add_argument("--tile-format", choices=("png", "webp"), default="webp",
                        help="tile encoding; webp is lossless here and ~3x smaller")
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--min-body-km2", type=float, default=config.BODY_MIN_AREA_KM2,
                        help="smallest discovered water body to catalogue and forecast")
    parser.add_argument("--max-bodies", type=int, default=config.BODY_MAX_COUNT,
                        help="cap on catalogued bodies, largest first")
    parser.add_argument("--mask-subsample", type=int, default=config.MASK_SUBSAMPLE,
                        help="class-layer sub-samples per grid cell (majority vote for "
                             "the water mask; 1 disables)")
    parser.add_argument("--no-forecast", action="store_true")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)

    grid = build_grid(tuple(args.bbox), max_dim=args.max_dim)
    windows = plan_windows(now, args.history_frames, args.frame_step_days,
                           args.frame_window_days)

    def progress(done: int, total: int, found: int) -> None:
        print(f"  searched {done}/{total} window-cells, {found} unique scenes", flush=True)

    ocean = load_ocean_mask(grid)
    if not ocean.any():
        print("ocean mask unavailable; only border-connected water is excluded",
              file=sys.stderr)
    ocean_cells = ocean_search_cells(ocean, grid, args.cell_deg) if ocean.any() else frozenset()
    if ocean_cells:
        print(f"  skipping {len(ocean_cells)} ocean-only search cells", flush=True)

    scene_windows = collect_scenes_by_window(
        tuple(args.bbox), windows, cell_deg=args.cell_deg, per_cell=args.per_cell,
        max_cloud=args.max_cloud, workers=args.workers, progress=progress,
        skip_cells=ocean_cells)
    total_scenes = sum(len(scenes) for scenes in scene_windows)
    if total_scenes == 0:
        print("no scenes found for the region", file=sys.stderr)
        return 1
    print(f"collected {total_scenes} usable scenes across {len(windows)} windows "
          f"({windows[0][0].date()} to {windows[-1][1].date()})", flush=True)

    # Date-filter every window's candidates first, so the shared pass folds each scene only
    # into the windows whose dates actually contain it.
    window_scenes = [scenes_in_window(scene_windows[index], start, end)
                     for index, (start, end) in enumerate(windows)]

    print("compositing windows in one pass over the deduped scene set ...", flush=True)
    started = time.perf_counter()

    def pass_progress(read: int, folds: int, done: int, windows_count: int) -> None:
        elapsed = time.perf_counter() - started
        rate = read / elapsed if elapsed > 0 else 0.0
        print(f"  {read} scenes read ({folds} folds, {done}/{windows_count} windows "
              f"saturated) · {rate:.1f} scenes/s · {elapsed / 60:.1f} min elapsed",
              flush=True)

    canvases, water_fractions, scenes_used = mosaic_windows(
        window_scenes, grid, assets=("blue", "green", "red", "rededge1", "scl"),
        workers=args.workers, max_scenes=args.max_scenes_per_frame,
        patience=args.saturation_patience, sub=args.mask_subsample,
        progress=pass_progress)
    print(f"read {sum(scenes_used)} scene-folds across {len(windows)} windows", flush=True)

    composites: list[dict | None] = []
    for index, canvas in enumerate(canvases):
        try:
            composite = compute_composite(canvas, water_fractions[index],
                                          window_scenes[index], grid, now=now,
                                          ocean=ocean)
            composite["scene_count"] = scenes_used[index]
        except Exception as exc:
            if index == len(windows) - 1:
                print(f"failed to build the latest frame: {exc}", file=sys.stderr)
                return 1
            print(f"window {index}: build failed ({exc}), skipped", flush=True)
            composite = None
        composites.append(composite)
        canvases[index] = None  # release the DN rasters as soon as they are used

    latest_composite = composites[-1]
    if latest_composite is None:
        print("the latest window produced no composite", file=sys.stderr)
        return 1

    # Coverage is derived from the data: sieve the composited water mask, take every patch
    # above the area threshold, and sample those. A hand-written list decided what was
    # covered before this, which is why whole states could be empty.
    bodies = discover_bodies(latest_composite["valid"], grid,
                             min_area_km2=args.min_body_km2,
                             max_bodies=args.max_bodies)
    curated_hits = sum(1 for body in bodies if body.curated)
    print(f"discovered {len(bodies)} water bodies >= {args.min_body_km2:.0f} km2 "
          f"({curated_hits} named, {len(bodies) - curated_hits} named by coordinates)",
          flush=True)
    if not bodies:
        print("no water bodies found in the latest composite", file=sys.stderr)
        return 1

    labels, footprint = body_labels(bodies, grid)
    measured = int((footprint > 0).sum())
    print(f"footprints rasterised: {measured} bodies, "
          f"median {int(np.median(footprint[footprint > 0]))} cells, "
          f"largest {int(footprint.max())} cells", flush=True)

    frames: list[dict] = []
    body_series: dict[str, list[dict]] = {body.id: [] for body in bodies}
    normalization: dict | None = None
    latest_risk: np.ndarray | None = None
    latest_frame: dict | None = None

    normalization = {
        "basis": "latest-observed",
        "ndci": [round(v, 6) for v in latest_composite["ndci_range"]],
        "ndti": [round(v, 6) for v in latest_composite["ndti_range"]],
        "clamped": True,
    }

    frame_index = 0
    for window_index, (window_start, window_end) in enumerate(windows):
        composite = composites[window_index]
        if composite is None or not window_scenes[window_index]:
            continue
        frame_id = f"obs-{frame_index:02d}"
        is_latest = window_index == len(windows) - 1

        risk = risk_from(composite, normalization["ndci"], normalization["ndti"])
        tile_rel, probe_rel = write_frame_tiles(args.out, frame_id, risk, grid,
                                                args.probe_max_dim, args.tile_format)

        scene_dates = composite["scene_dates"]
        frames.append({
            "id": frame_id,
            "kind": "observed",
            "label": window_end.strftime("%b %d"),
            "window_label": f"{window_start.strftime('%b %d')} - "
                            f"{window_end.strftime('%b %d')}",
            "date": window_end.strftime("%Y-%m-%d"),
            "tile": tile_rel,
            "probe_tile": probe_rel,
            "latest": is_latest,
            "age_days": round(composite["median_age"], 2)
            if composite["median_age"] is not None else None,
            "source_scene": composite["source_scene"],
            "scene_count": composite["scene_count"],
            "scene_dates": [scene_dates[0], scene_dates[-1]],
            "ndti_baseline": [round(v, 6) for v in composite["ndti_range"]],
            "ndci_baseline": [round(v, 6) for v in composite["ndci_range"]],
            "bloom_fraction": round(composite["bloom_fraction"], 4),
            "coverage": {
                "satellite_valid_fraction": round(
                    composite["coverage"]["satellite_valid_fraction"], 4),
                "water_fraction_of_grid": round(
                    composite["coverage"]["water_fraction_of_grid"], 4),
            },
        })

        samples = sample_bodies(bodies, frame_id, risk, composite["ndci"],
                                composite["ndti"], composite["valid"],
                                composite["water_fraction"], grid,
                                include_indices=True, labels=labels,
                                footprint=footprint)
        for body_id, entry in samples.items():
            body_series[body_id].append(entry)
        frame_index += 1

        print(f"{frame_id} {frames[-1]['window_label']}: "
              f"{composite['scene_count']}/{len(window_scenes[window_index])} scenes read, "
              f"median age {frames[-1]['age_days']}d, "
              f"valid {100 * composite['coverage']['satellite_valid_fraction']:.1f}%, "
              f"water {100 * composite['coverage']['water_fraction_of_grid']:.2f}%"
              f"{'  [latest]' if is_latest else ''}", flush=True)

        if is_latest:
            latest_risk = risk
            latest_frame = frames[-1]

    if normalization is None or latest_risk is None or latest_frame is None:
        print("no frames could be built", file=sys.stderr)
        return 1

    forecast_dates: list[str] = []
    forecast_summary: dict | None = None
    if not args.no_forecast:
        try:
            rainfall = fetch_rainfall_field(*grid["bbox"], days=args.forecast_days)
            fields = anomaly_fields(rainfall, grid["height"], grid["width"])
            forecast_dates = list(rainfall["dates"])[:len(fields)]
            observed = np.nan_to_num(latest_risk, nan=0.0)
            water = np.isfinite(latest_risk)

            for day_index, (day, field) in enumerate(zip(forecast_dates, fields), start=1):
                frame_id = f"fc-{day_index:02d}"
                risk = np.clip(observed + field, 0.0, 1.0)
                risk[~water] = np.nan
                tile_rel, probe_rel = write_frame_tiles(args.out, frame_id, risk, grid,
                                                        args.probe_max_dim,
                                                        args.tile_format)
                frames.append({
                    "id": frame_id,
                    "kind": "forecast",
                    "label": f"+{day_index}d",
                    "window_label": datetime.fromisoformat(day).strftime("%a %b %d"),
                    "date": day,
                    "forecast_day": day_index,
                    "tile": tile_rel,
                    "probe_tile": probe_rel,
                    "latest": False,
                    "age_days": None,
                    "scene_count": 0,
                    "anomaly_mean": round(float(np.nanmean(field[water])), 4)
                    if water.any() else None,
                })
                samples = sample_bodies(bodies, frame_id, risk,
                                        np.full_like(risk, np.nan),
                                        np.full_like(risk, np.nan),
                                        np.isfinite(risk),
                                        np.isfinite(risk).astype("float32"), grid,
                                        include_indices=False, labels=labels,
                                        footprint=footprint)
                for body_id, entry in samples.items():
                    body_series[body_id].append(entry)
                print(f"{frame_id} +{day_index}d: mean loading anomaly "
                      f"{frames[-1]['anomaly_mean']}", flush=True)

            region_rain = [round(float(np.mean(day)), 2) for day in rainfall["rain"]]
            forecast_summary = {
                "horizon_days": len(forecast_dates),
                "dates": forecast_dates,
                "region_rain_mm": region_rain,
                "basis": FORECAST_BASIS,
                "assumptions": {
                    "land_cover": "row_crop",
                    "soil_group": "B",
                    "anomaly_divisor": ANOMALY_DIVISOR,
                },
            }
        except Exception as exc:
            print(f"forecast skipped: {exc}", file=sys.stderr)

    water_bodies = []
    for body in bodies:
        rc = body_rc(grid, body)
        series = body_series[body.id]
        observed = [entry for entry in series
                    if entry["frame"].startswith("obs") and entry.get("valid")]
        latest_entry = next((entry for entry in reversed(series)
                             if entry["frame"].startswith("obs")), None)
        rain: list[float] = []
        if rc is not None and forecast_dates:
            rain = _daily_rainfall_sample(rainfall, grid, rc[1], rc[0])
        entry = {
            "id": body.id,
            "name": body.name,
            "kind": body.kind,
            "lat": round(body.lat, 4),
            "lon": round(body.lon, 4),
            "area_km2": body.area_km2,
            "curated": body.curated,
            "caution": body.caution,
            "series": series,
            "rain_mm": rain,
            # The ranked value: the body's areal mean, not its worst cell.
            "latest_risk": latest_entry["risk"] if latest_entry else None,
            "latest_peak": latest_entry.get("risk_peak") if latest_entry else None,
            "latest_std": latest_entry.get("risk_std") if latest_entry else None,
            "latest_cells": latest_entry.get("cells") if latest_entry else None,
            "latest_cell_fraction": (latest_entry.get("cell_fraction")
                                     if latest_entry else None),
            "latest_ndci": latest_entry.get("ndci") if latest_entry else None,
            "latest_ndti": latest_entry.get("ndti") if latest_entry else None,
            "observed_frames": len(observed),
            "total_frames": len(series),
        }
        if rain and latest_entry and latest_entry["risk"] is not None:
            # The forecast is per body, from that body's own rainfall, with the body's last
            # real observation as the baseline. Its inputs travel with it so a reader can
            # check the arithmetic rather than trust a line on a chart.
            antecedent = 0.0
            if len(rainfall["rain"]) > 0 and rc is not None:
                antecedent = _antecedent_rainfall_sample(rainfall, grid, rc[1], rc[0])
            entry["forecast"] = body_forecast_series(
                latest_entry["risk"], rain, antecedent)
        water_bodies.append(entry)

    coverage = latest_frame["coverage"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "region_name": config.REGION_NAME,
        "region_bbox": list(grid["bbox"]),
        "discovery": {
            "min_body_km2": args.min_body_km2,
            "max_bodies": args.max_bodies,
            "found": len(bodies),
            "named": curated_hits,
            "mask_subsample": args.mask_subsample,
            "water_threshold": config.MASK_WATER_FRACTION,
        },
        "resolution_m": grid["res_m"],
        "requested_resolution_m": grid["requested_res_m"],
        "grid": {
            "width": grid["width"],
            "height": grid["height"],
            "crs": grid["crs"],
            "merc_bounds": list(grid["merc_bounds"]),
        },
        "probe": {
            "width": len(range(0, grid["width"], probe_stride(grid, args.probe_max_dim))),
            "height": len(range(0, grid["height"], probe_stride(grid, args.probe_max_dim))),
            "sample_m": round(grid["res_m"] * probe_stride(grid, args.probe_max_dim), 1),
        },
        "palette": [[stop, list(colour)] for stop, colour in PALETTE_STOPS],
        "scales": {"ndci": list(config.NDCI_BANDS), "ndti": list(config.NDTI_BANDS)},
        "bloom_threshold_ndci": config.BLOOM_NDCI_THRESHOLD,
        "ramp_semantics": "relative to the latest observed composite's p05-p95 range",
        "body_statistic": (
            "per-body risk is the areal mean over the body's own footprint; risk_peak is "
            "the worst single cell in it. A point sample at one cell was the previous "
            "behaviour and is what the peak column preserves."),
        "normalization": normalization,
        "forecast": forecast_summary,
        "coverage": {
            "satellite_valid_fraction": coverage["satellite_valid_fraction"],
            "water_fraction_of_grid": coverage["water_fraction_of_grid"],
            "scenes_mosaicked": latest_frame["scene_count"],
        },
        "water_bodies": water_bodies,
        "frames": frames,
        "disclaimer": DISCLAIMER,
    }
    path = write_manifest(args.out, manifest)
    keep = {Path(frame["tile"]).name for frame in frames}
    keep |= {Path(frame["probe_tile"]).name for frame in frames}
    pruned = prune_tiles(args.out, keep)
    print(f"wrote {path}" + (f" (pruned {pruned} stale tiles)" if pruned else ""))
    print(f"  grid {grid['width']}x{grid['height']} @ {grid['res_m']:.0f} m  "
          f"frames={len(frames)} ({sum(1 for f in frames if f['kind'] == 'observed')} observed"
          f" + {sum(1 for f in frames if f['kind'] == 'forecast')} forecast)  "
          f"water={100 * coverage['water_fraction_of_grid']:.2f}%  "
          f"bodies={sum(1 for b in water_bodies if b['latest_risk'] is not None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
