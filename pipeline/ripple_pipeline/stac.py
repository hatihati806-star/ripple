"""Rule R2 -- scene discovery and AOI-coverage ranking.

Cloud cover is a whole-granule statistic and says nothing about the AOI. For a western
Lake Erie AOI the *clearest* available scene (0.00% cloud) covered only 41.4% of the AOI,
because the granule is a rotated parallelogram that merely clipped the corner. Ranking by
AOI valid coverage instead found a 100% scene.

We therefore rank candidate scenes by how much non-nodata data they actually deliver
inside the AOI.
"""
from __future__ import annotations

import json
import urllib.request

from .config import EARTH_SEARCH, S2_COLLECTION

_SEARCH_TIMEOUT_S = 60


def search_scenes(bbox, start: str, end: str, limit: int = 40,
                  collection: str = S2_COLLECTION) -> list[dict]:
    """STAC search for Sentinel-2 L2A items intersecting `bbox` within [start, end]."""
    body = json.dumps({
        "bbox": list(bbox),
        "collections": [collection],
        "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z",
        "limit": int(limit),
    }).encode()
    req = urllib.request.Request(
        f"{EARTH_SEARCH}/search", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT_S) as resp:
        return json.loads(resp.read())["features"]


def aoi_coverage(scene: dict, bbox, probe_asset: str = "green") -> float:
    """Fraction of the AOI carrying valid (non-nodata) data in this scene."""
    from .cog import read_aoi  # local import avoids a module-load cycle

    _, valid = read_aoi(scene, probe_asset, bbox)
    return float(valid.mean())


def rank_by_aoi_coverage(scenes: list[dict], bbox) -> list[tuple[float, dict]]:
    """Sort scenes best-coverage-first. Scenes that cannot be read are dropped."""
    ranked: list[tuple[float, dict]] = []
    for scene in scenes:
        try:
            ranked.append((aoi_coverage(scene, bbox), scene))
        except Exception:
            continue
    ranked.sort(key=lambda pair: (
        -pair[0], pair[1]["properties"].get("eo:cloud_cover", 100.0)))
    return ranked


def pick_best_scene(scenes: list[dict], bbox) -> tuple[float, dict]:
    """Return ``(coverage_fraction, scene)`` for the scene best covering the AOI.

    Among scenes with near-complete coverage, prefer the least cloudy.
    """
    ranked = rank_by_aoi_coverage(scenes, bbox)
    if not ranked:
        raise RuntimeError("no readable scenes for this AOI")

    best_frac = ranked[0][0]
    if best_frac < 0.99:
        return ranked[0]

    near_complete = [pair for pair in ranked if pair[0] >= 0.99]
    near_complete.sort(key=lambda pair: pair[1]["properties"].get("eo:cloud_cover", 100.0))
    return near_complete[0]


def collect_scenes_for_region(bbox, start: str, end: str, cell_deg: float = 1.5,
                              per_cell: int = 6, max_cloud: float = 60.0,
                              progress=None) -> list[dict]:
    """Collect deduped scenes covering a large region.

    A single STAC search returns an arbitrary handful of granules, which over a region
    spanning hundreds of granules is a fraction of a percent of the area. This splits the
    region into search cells (each larger than one 110 km granule), queries them
    individually, and dedupes by scene id.

    Scenes above `max_cloud` are discarded -- reading a fully overcast granule is pure
    cost with no usable pixels.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    seen: dict[str, dict] = {}

    lat = lat_min
    cell_index = 0
    while lat < lat_max:
        lon = lon_min
        while lon < lon_max:
            cell = (lon, lat, min(lon + cell_deg, lon_max), min(lat + cell_deg, lat_max))
            cell_index += 1
            try:
                for scene in search_scenes(cell, start, end, limit=per_cell):
                    cloud = scene["properties"].get("eo:cloud_cover", 100.0)
                    if cloud <= max_cloud:
                        seen[scene["id"]] = scene
            except Exception:
                pass
            if progress is not None:
                progress(cell_index, len(seen))
            lon += cell_deg
        lat += cell_deg

    return list(seen.values())


def _bbox_cells(bbox, cell_deg: float):
    lon_min, lat_min, lon_max, lat_max = bbox
    lat = lat_min
    while lat < lat_max:
        lon = lon_min
        while lon < lon_max:
            yield (lon, lat, min(lon + cell_deg, lon_max), min(lat + cell_deg, lat_max))
            lon += cell_deg
        lat += cell_deg


def collect_scenes_by_window(bbox, windows, cell_deg: float = 1.5, per_cell: int = 12,
                             max_cloud: float = 60.0, workers: int = 8,
                             progress=None,
                             skip_cells: frozenset = frozenset()) -> list[list[dict]]:
    """Collect scenes per time window, one search pass per window per cell.

    STAC returns the newest items first for a requested limit, so one search over the whole
    lookback can only ever see the most recent weeks -- the oldest windows of a radar loop
    silently starve, and the animation opens on a half-empty map. Searching each window
    separately costs one extra pass per frame and guarantees every frame has its own
    candidate set.

    ``skip_cells`` holds ``(lon, lat)`` cell origins (see ``cli.ocean_search_cells``) whose
    area cannot contribute anything; skipping them saves pure search and read cost.

    ``windows`` are ``(start, end)`` datetimes, end inclusive to the day; returns one scene
    list per window.
    """
    import datetime as _dt
    from concurrent.futures import ThreadPoolExecutor

    cells = [cell for cell in _bbox_cells(bbox, cell_deg)
             if (cell[0], cell[1]) not in skip_cells]
    tasks = []
    for index, (start, end) in enumerate(windows):
        start_date = start.strftime("%Y-%m-%d")
        end_date = (end + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        for cell in cells:
            tasks.append((index, cell, start_date, end_date))

    def fetch(task):
        index, cell, start_date, end_date = task
        try:
            return index, search_scenes(cell, start_date, end_date, limit=per_cell)
        except Exception:
            return index, []

    per_window: list[dict[str, dict]] = [dict() for _ in windows]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, scenes in pool.map(fetch, tasks, chunksize=8):
            done += 1
            for scene in scenes:
                if scene["properties"].get("eo:cloud_cover", 100.0) <= max_cloud:
                    per_window[index][scene["id"]] = scene
            if progress is not None and done % 40 == 0:
                progress(done, len(tasks), sum(len(window) for window in per_window))

    return [list(window.values()) for window in per_window]
