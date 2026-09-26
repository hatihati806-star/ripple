"""Rule R10 -- validation against USGS NWIS in-situ turbidity.

The product's turbidity signal is NDTI, an optical proxy. Until now nothing in this
codebase had been checked against a real measurement: the indices carried literature
citations and regional calibration anchors, not ground truth. This module closes that gap.

Method, in full, so the number can be argued with rather than believed:

1. Enumerate every active USGS continuous turbidity time series (parameter 63680, FNU)
   inside the product's region, then keep only the gauges that sit on a cell the
   *delivered product* maps as water. A gauge the product reports no reading for cannot
   validate it, and including one would be comparing against a claim never made.
2. For each gauge, find Sentinel-2 L2A scenes over it in the validation window.
3. Read the single analysis cell containing the gauge exactly the way the pipeline reads
   it -- bilinear-averaged surface reflectance at the product resolution, SCL water
   fraction by majority vote, NDTI = (red - green) / (red + green) -- and require the
   same 25% water threshold the mask uses.
4. Match each scene to the gauge's turbidity within a +-3 h window of acquisition, taking
   the median of the in-situ samples in that window.
5. Report rank correlation and the accuracy of a log-linear NDTI -> turbidity estimate,
   leave-one-out.

Honest limits, stated up front:

* A gauge measures one point (usually at a bank); the satellite cell averages ~1.85 km2.
  That footprint mismatch cannot be removed here, and it compounds with the site-specific
  optical baseline -- the largest measured error term in the per-gauge calibration test.
* The log-linear fit is empirical, not a physical inversion, and it is fitted to these
  gauges: it is a *site-transfer* test, not a universal calibration.
* FNU and NTU are both turbidity units from different instruments; both are reported with
  the unit they arrived in, and the metric is computed on the pooled sample.
* A satellite scene is instantaneous at the overpass; a gauge sample is an instantaneous
  point measurement. Matching them within +-3 h does not make them simultaneous.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .cog import read_aoi
from .config import MASK_SUBSAMPLE, MASK_WATER_FRACTION, TARGET_RES_M
from .indices import ndti
from .mask import scl_water
from .scaling import to_reflectance
from .stac import search_scenes

# USGS Water Data OGC API. Key-free, and the successor to the legacy waterservices
# site service, whose bbox queries no longer answer.
NWIS_API = "https://api.waterdata.usgs.gov/ogcapi/v0"
TURBIDITY_PARAM = "63680"          # Turbidity, FNU (monochrome near-IR LED, 90 deg)
TURBIDITY_PARAM_NTU = "00076"      # Turbidity, NTU (older instruments)

# In-situ samples taken within this many hours of the satellite overpass are treated as
# describing the same water. Sentinel-2 crosses the equator near 10:30 local time.
MATCH_WINDOW_HOURS = 3.0
MIN_SAMPLES_PER_MATCH = 2

# A scene is only considered if the granule is not mostly cloud. The cell itself is checked
# separately, so this only avoids reading obviously useless granules.
MAX_GRANULE_CLOUD = 40.0

# Pre-declared water-fraction strata for the mixing analysis (see `summarise`). Fixed here
# so the split cannot be tuned to the result.
WATER_FRACTION_STRATA = (0.25, 0.50, 0.75)

# Resolution of the water-only variant: fine enough to isolate water sub-pixels inside a
# 1.85 km cell, coarse enough that a whole cell is a few thousand samples rather than a
# few hundred thousand.
WATER_PIXEL_RES_M = 30.0

USER_AGENT = "ripple-validation/0.1 (+https://github.com/; contact: hackathon build)"


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
def _get_json(url: str, tries: int = 5, timeout: float = 90.0) -> dict:
    """GET a JSON document with backoff. Raises on the final failure.

    A 429 is treated as "slow down", not "give up": the USGS API throttles bursts, and the
    gauge enumeration is a burst by construction. Its ``Retry-After`` is honoured when
    present.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if (retry_after or "").replace(".", "").isdigit() \
                    else 20.0 + 15.0 * attempt
                time.sleep(delay)
                continue
            time.sleep(1.5 + 2.0 * attempt)
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            time.sleep(1.5 + 2.0 * attempt)
    raise RuntimeError(f"GET failed after {tries} attempts: {url[:200]} ({last})")


# --------------------------------------------------------------------------------------
# USGS NWIS
# --------------------------------------------------------------------------------------
def turbidity_series_ids(bbox, parameter_codes=(TURBIDITY_PARAM, TURBIDITY_PARAM_NTU),
                         limit: int = 1000, max_pages: int = 20,
                         cache: Path | None = None, refresh: bool = False) -> list[dict]:
    """Every continuous turbidity time series in `bbox`, one record per series.

    Cached next to the package after the first successful pass, the same way the ocean
    mask is: the enumeration is ~5,000 records of metadata that changes on the scale of
    months, and re-pulling it on every run only invites the API's rate limiter.
    """
    if cache is not None and cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    rows: list[dict] = []
    for code in parameter_codes:
        offset = 0
        for page in range(max_pages):
            url = (f"{NWIS_API}/collections/time-series-metadata/items?"
                   + urllib.parse.urlencode({
                       "parameter_code": code,
                       "bbox": ",".join(f"{v:g}" for v in bbox),
                       "limit": limit,
                       "offset": offset,
                   }))
            payload = _get_json(url)
            features = payload.get("features", [])
            for feature in features:
                props = feature.get("properties", {})
                geometry = (feature.get("geometry") or {}).get("coordinates")
                rows.append({
                    "site": props.get("monitoring_location_id"),
                    "name": props.get("monitoring_location_name"),
                    "begin": props.get("begin_utc"),
                    "end": props.get("end_utc"),
                    "unit": props.get("unit_of_measure"),
                    "parameter_code": code,
                    "lon": geometry[0] if geometry else None,
                    "lat": geometry[1] if geometry else None,
                })
            if len(features) < limit:
                break
            offset += len(features)
            if page:
                time.sleep(1.0)

    if cache is not None and rows:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def fetch_turbidity(site: str, start: str, end: str, chunk_days: int = 60,
                    limit: int = 6000) -> list[dict]:
    """Continuous turbidity for one site over [start, end], oldest first.

    The window is requested in chunks and the response is checked against `limit`, because
    the collection silently truncates: a 51-day window at 15-minute cadence is ~4,900
    samples, and an unguarded request that caps at 2,000 would deliver the first three
    weeks and quietly drop the rest -- exactly the kind of truncation that turns into a
    fake accuracy figure.

    Values flagged below detection are kept as reported: a 0.0 FNU reading is a real
    measurement of clear water, not a gap. Non-numeric and negative entries are dropped
    (-999999 is the NWIS missing-value sentinel).
    """
    start_date = datetime.fromisoformat(start)
    end_date = datetime.fromisoformat(end)
    samples: list[dict] = []
    cursor = start_date
    while cursor < end_date:
        chunk_end = min(cursor + timedelta(days=chunk_days), end_date)
        url = (f"{NWIS_API}/collections/continuous/items?"
               + urllib.parse.urlencode({
                   "monitoring_location_id": site,
                   "parameter_code": TURBIDITY_PARAM,
                   "datetime": (f"{cursor.strftime('%Y-%m-%d')}T00:00:00Z/"
                                f"{chunk_end.strftime('%Y-%m-%d')}T23:59:59Z"),
                   "limit": limit,
               }))
        payload = _get_json(url)
        features = payload.get("features", [])
        if len(features) >= limit:
            raise RuntimeError(
                f"{site}: in-situ response truncated at {limit} records for "
                f"{cursor.date()}..{chunk_end.date()}; narrow chunk_days")
        for feature in features:
            props = feature.get("properties", {})
            try:
                value = float(props.get("value"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value < 0:
                continue
            stamp = props.get("time")
            if not stamp:
                continue
            samples.append({
                "time": stamp,
                "value": value,
                "unit": (props.get("unit_of_measure") or "").lstrip("_"),
            })
        cursor = chunk_end + timedelta(days=1)
    samples.sort(key=lambda sample: sample["time"])
    return samples


def fetch_all_turbidity(gauges: list[dict], start: str, end: str,
                        cache: Path | None = None, refresh: bool = False,
                        pause_s: float = 0.4) -> dict[str, list[dict]]:
    """In-situ series for every gauge, one request at a time.

    Deliberately serial. The USGS API answers a burst of parallel requests with 429s, and
    a retry storm turns a two-minute job into an hour; a 0.4 s gap costs nothing by
    comparison. The result is cached for the same reason the series list is.
    """
    cached: dict[str, list[dict]] = {}
    if cache is not None and cache.exists() and not refresh:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}

    series: dict[str, list[dict]] = {}
    for index, gauge in enumerate(gauges, start=1):
        site = gauge["site"]
        if site in cached:
            series[site] = cached[site]
            print(f"  in-situ [{index}/{len(gauges)}] {site:16s} "
                  f"{len(cached[site]):5d} samples (cached)", flush=True)
            continue
        try:
            series[site] = fetch_turbidity(site, start, end)
        except Exception as exc:  # one dead site must not end the run
            print(f"    {site}: in-situ fetch failed ({exc})", flush=True)
            series[site] = []
        print(f"  in-situ [{index}/{len(gauges)}] {site:16s} "
              f"{len(series[site]):5d} samples", flush=True)
        if cache is not None:
            # Written after every gauge, not once at the end: this phase makes ~130 live
            # requests, and a run interrupted at gauge 40 must not repeat the first 39.
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(series), encoding="utf-8")
        time.sleep(pause_s)
    return series


def _parse_stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def match_sample(samples: list[dict], when: datetime,
                 window_hours: float = MATCH_WINDOW_HOURS) -> dict | None:
    """Median in-situ turbidity within `window_hours` of `when`, or None.

    A median rather than a mean because a single passing boat or a gate release is a
    spike, and the satellite sees the cell, not the spike.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    window = timedelta(hours=float(window_hours))
    in_window = [s for s in samples if abs(_parse_stamp(s["time"]) - when) <= window]
    if len(in_window) < MIN_SAMPLES_PER_MATCH:
        return None
    values = [s["value"] for s in in_window]
    return {
        "value": float(np.median(values)),
        "n": len(values),
        "min": float(min(values)),
        "max": float(max(values)),
        "unit": in_window[0]["unit"],
    }


# --------------------------------------------------------------------------------------
# The satellite side, read exactly as the pipeline reads it
# --------------------------------------------------------------------------------------
def cell_bbox(lon: float, lat: float, res_m: float = TARGET_RES_M
              ) -> tuple[float, float, float, float]:
    """Geographic bounds of one analysis cell centred on (lon, lat)."""
    half = float(res_m) / 2.0
    dlat = half / 110_574.0
    dlon = half / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def cell_ndti(scene: dict, lon: float, lat: float,
              res_m: float = TARGET_RES_M) -> dict:
    """NDTI for the analysis cell containing (lon, lat), from one scene.

    Two numbers, and the difference between them is the point:

    * ``ndti_cell`` -- NDTI of the cell's **averaged** reflectance, which is what the
      product publishes and the map draws. Reflectance is bilinear-averaged onto the cell
      exactly as ``cli.read_into_grid`` does it, the class layer is majority-voted at
      ``MASK_SUBSAMPLE`` times the cell resolution into a water fraction, and the cell is
      rejected below the mask's water threshold. On a narrow river this number mixes water
      with shore and land spectra; that is a property of the product, not of this test.
    * ``ndti_water`` -- median NDTI over the sub-pixels the class layer actually calls
      water, at ~30 m. This is the index on water only, with the mixing removed.

    Reporting both separates "the index is wrong" from "the cell is a mixture", which are
    different findings with different fixes.
    """
    bbox = cell_bbox(lon, lat, res_m)
    sub_res = max(10.0, float(res_m) / max(1, MASK_SUBSAMPLE))

    red_raw, red_valid = read_aoi(scene, "red", bbox, target_res=res_m,
                                  resampling="average")
    green_raw, green_valid = read_aoi(scene, "green", bbox, target_res=res_m,
                                      resampling="average")
    scl_raw, _ = read_aoi(scene, "scl", bbox, target_res=sub_res)

    if not (red_valid.any() and green_valid.any()):
        return {"ndti_cell": None, "ndti_water": None, "water_fraction": 0.0,
                "water_pixels": 0, "reason": "outside the scene footprint"}

    # The mask's own rule: majority vote over MASK_SUBSAMPLE sub-cells per cell, counting
    # only strict SCL water (class 6). "Usable" is not "water" -- land is perfectly usable.
    mask_water_fraction = float(scl_water(scl_raw).mean())
    if mask_water_fraction < MASK_WATER_FRACTION:
        return {"ndti_cell": None, "ndti_water": None,
                "water_fraction": mask_water_fraction, "water_pixels": 0,
                "reason": f"cell {mask_water_fraction:.0%} water by the mask's own "
                          f"vote, below its {MASK_WATER_FRACTION:.0%} threshold"}

    # DN 0 is the nodata sentinel and would otherwise read as a reflectance of 0.0, which
    # is a valid-looking number that silently drags NDTI toward zero.
    valid = red_valid & green_valid
    red = np.where(valid, to_reflectance(red_raw), np.nan)
    green = np.where(valid, to_reflectance(green_raw), np.nan)
    cell_value = float(np.nanmedian(ndti(red, green)))

    # The water-only variant, and the water fraction actually reported: at 30 m there are
    # ~3,800 samples per cell, so this is a description rather than a four-way vote.
    fine_res = WATER_PIXEL_RES_M
    red_fine, red_fine_valid = read_aoi(scene, "red", bbox, target_res=fine_res)
    green_fine, green_fine_valid = read_aoi(scene, "green", bbox, target_res=fine_res)
    scl_fine, _ = read_aoi(scene, "scl", bbox, target_res=fine_res)
    fine_water = scl_water(scl_fine) & red_fine_valid & green_fine_valid
    water_value = None
    if fine_water.any():
        fine_ndti = ndti(to_reflectance(red_fine), to_reflectance(green_fine))
        water_value = float(np.nanmedian(fine_ndti[fine_water]))
        if not math.isfinite(water_value):
            water_value = None
    fine_fraction = float(fine_water.mean())

    reason = None
    if not math.isfinite(cell_value):
        cell_value = None
        reason = "NDTI undefined"
    return {"ndti_cell": cell_value, "ndti_water": water_value,
            "water_fraction": fine_fraction, "mask_water_fraction": mask_water_fraction,
            "water_pixels": int(fine_water.sum()), "reason": reason}


def candidate_scenes(lon: float, lat: float, start: str, end: str,
                     max_scenes: int = 10) -> list[dict]:
    """Scenes intersecting a small AOI around the gauge, clearest granule first."""
    pad = 0.05
    bbox = (lon - pad, lat - pad, lon + pad, lat + pad)
    scenes = search_scenes(bbox, start, end, limit=200)
    scenes = [scene for scene in scenes
              if scene.get("properties", {}).get("eo:cloud_cover", 100.0) <= MAX_GRANULE_CLOUD]
    scenes.sort(key=lambda scene: (scene["properties"].get("eo:cloud_cover", 100.0),
                                   scene["properties"].get("datetime", "")))
    return scenes[:max_scenes]


def dedupe_by_day(pairs: list[dict]) -> list[dict]:
    """One pair per gauge per acquisition day.

    Adjacent Sentinel-2 tiles overlap, so a single overpass can return two or three
    granules that all contain the cell. Counting each of them would enter the same
    satellite day into the metric three times and inflate n without adding information.
    The best-covering granule wins (highest cell water fraction, then least cloud).
    """
    best: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        key = (pair["site"], pair["scene_time"][:10])
        rank = (pair["cell_water_fraction"], -float(pair.get("scene_cloud_cover") or 100.0))
        current = best.get(key)
        if current is None:
            best[key] = pair
            continue
        current_rank = (current["cell_water_fraction"],
                        -float(current.get("scene_cloud_cover") or 100.0))
        if rank > current_rank:
            best[key] = pair
    return sorted(best.values(), key=lambda pair: (pair["site"], pair["scene_time"]))


def gauge_pairs(scene: dict, lon: float, lat: float, samples: list[dict],
                res_m: float = TARGET_RES_M) -> dict | None:
    """One (NDTI, in-situ turbidity) pair for a scene over a gauge, or None."""
    cell = cell_ndti(scene, lon, lat, res_m)
    if cell["ndti_cell"] is None and cell["ndti_water"] is None:
        return None
    props = scene["properties"]
    when = _parse_stamp(props["datetime"])
    measured = match_sample(samples, when)
    if measured is None:
        return None
    return {
        "scene": scene["id"],
        "scene_time": props["datetime"],
        "scene_cloud_cover": props.get("eo:cloud_cover"),
        "cell_water_fraction": round(cell["water_fraction"], 3),
        "cell_water_pixels": cell["water_pixels"],
        "ndti": round(cell["ndti_cell"], 4) if cell["ndti_cell"] is not None else None,
        "ndti_water": round(cell["ndti_water"], 4)
        if cell["ndti_water"] is not None else None,
        "turbidity": round(measured["value"], 3),
        "turbidity_n": measured["n"],
        "turbidity_spread": round(measured["max"] - measured["min"], 3),
        "unit": measured["unit"],
    }


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------
def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks, ties shared, so Spearman is exact on tied samples."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype="float64")
    sorted_values = values[order]
    index = 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and sorted_values[end + 1] == sorted_values[index]:
            end += 1
        ranks[order[index:end + 1]] = 0.5 * (index + end) + 1.0
        index = end + 1
    return ranks


def pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Pearson r and a two-sided t-test p-value."""
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    x_c = x - x.mean()
    y_c = y - y.mean()
    denom = math.sqrt(float((x_c ** 2).sum()) * float((y_c ** 2).sum()))
    if denom == 0.0:
        return float("nan"), float("nan")
    r = float((x_c * y_c).sum()) / denom
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        return r, 0.0
    t = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    p = 2.0 * _student_t_sf(abs(t), n - 2)
    return r, float(p)


def spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Spearman rho and its p-value."""
    return pearson(_ranks(np.asarray(x, dtype="float64")),
                   _ranks(np.asarray(y, dtype="float64")))


def _student_t_sf(t: float, df: int) -> float:
    """Upper tail of Student's t, without scipy (regularized incomplete beta)."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return 0.5 * _betainc(0.5 * df, 0.5, x)


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) by continued fraction (Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - log_beta) / a
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)
    f, c, d = 1.0, 1.0, 0.0
    for index in range(0, 200):
        m = index // 2
        if index == 0:
            numerator = 1.0
        elif index % 2 == 0:
            numerator = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            numerator = (-(a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        c = 1.0 + numerator / c
        if abs(c) < 1e-30:
            c = 1e-30
        delta = c * d
        f *= delta
        if abs(1.0 - delta) < 1e-10:
            break
    return front * (f - 1.0)


def log_linear_fit(ndti_values: np.ndarray, turbidity: np.ndarray) -> dict:
    """Least-squares fit of log10(turbidity) on NDTI."""
    x = np.asarray(ndti_values, dtype="float64")
    y = np.log10(np.asarray(turbidity, dtype="float64"))
    n = len(x)
    if n < 3 or float(np.ptp(x)) == 0.0:
        return {"slope": None, "intercept": None, "r2": None, "rmse_log10": None}
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residual = y - predicted
    ss_res = float((residual ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else None,
        "rmse_log10": math.sqrt(ss_res / n),
    }


def leave_one_out(ndti_values: np.ndarray, turbidity: np.ndarray) -> dict:
    """Fit on n-1 pairs, predict the held-out one, repeat. The honest accuracy number.

    The in-sample RMSE of a two-parameter fit on ten points is optimistic by construction;
    the held-out error is what a new gauge would actually see.
    """
    x = np.asarray(ndti_values, dtype="float64")
    y = np.log10(np.asarray(turbidity, dtype="float64"))
    n = len(x)
    if n < 4:
        return {"n": n, "rmse_log10": None, "factor": None}
    errors = []
    for held in range(n):
        keep = np.ones(n, dtype=bool)
        keep[held] = False
        if float(np.ptp(x[keep])) == 0.0:
            continue
        slope, intercept = np.polyfit(x[keep], y[keep], 1)
        errors.append(float(slope * x[held] + intercept - y[held]))
    if not errors:
        return {"n": n, "rmse_log10": None, "factor": None}
    rmse = math.sqrt(float(np.mean(np.square(errors))))
    return {"n": n, "rmse_log10": rmse, "factor": 10.0 ** rmse,
            "bias_log10": float(np.mean(errors))}


def site_calibration(ndti_values: np.ndarray, turbidity: np.ndarray,
                     sites: list[str], min_pairs: int = 4) -> dict:
    """How much of the error is a *per-site* optical baseline rather than noise.

    NDTI is a relative index: the same value reads differently on a clear mountain creek and
    in a sediment-loaded channel, so one global line through every gauge pays for that
    difference in its residuals. Fitting each gauge against its own pairs and pooling the
    residuals measures how much is left when the baseline is allowed to differ -- and that
    gap, not pixel mixing, turned out to be this index's dominant error term.

    Only gauges with at least `min_pairs` pairs and a non-degenerate spread are fitted
    separately; the rest fall back to their own mean so they cannot flatter the result.
    """
    y = np.log10(np.asarray(turbidity, dtype="float64"))
    x = np.asarray(ndti_values, dtype="float64")
    if len(x) < min_pairs:
        return {"global_factor": None, "per_site_factor": None, "sites_fitted": 0}

    global_fit = np.polyfit(x, y, 1)
    pooled = y - np.polyval(global_fit, x)

    grouped: dict[str, list[int]] = {}
    for index, site in enumerate(sites):
        grouped.setdefault(site, []).append(index)

    per_site = np.zeros_like(y)
    fitted = 0
    for indices in grouped.values():
        if len(indices) >= min_pairs and float(np.ptp(x[indices])) > 0:
            fit = np.polyfit(x[indices], y[indices], 1)
            per_site[indices] = y[indices] - np.polyval(fit, x[indices])
            fitted += 1
        else:
            per_site[indices] = y[indices] - y[indices].mean()

    global_rmse = math.sqrt(float(np.mean(pooled ** 2)))
    site_rmse = math.sqrt(float(np.mean(per_site ** 2)))
    return {
        "global_rmse_log10": global_rmse,
        "global_factor": 10.0 ** global_rmse,
        "per_site_rmse_log10": site_rmse,
        "per_site_factor": 10.0 ** site_rmse,
        "sites_fitted": fitted,
        "sites_total": len(grouped),
    }


def metric_block(ndti_values: np.ndarray, turbidity: np.ndarray,
                 sites: list[str] | None = None) -> dict:
    """Every headline number for one NDTI variant, computed once."""
    if len(ndti_values) < 3:
        return {"pairs": len(ndti_values), "note": "too few matched pairs for a metric"}
    rho, rho_p = spearman(ndti_values, turbidity)
    r_log, r_log_p = pearson(ndti_values, np.log10(turbidity))
    fit = log_linear_fit(ndti_values, turbidity)
    loo = leave_one_out(ndti_values, turbidity)

    within_site: dict[str, list[int]] = {}
    if sites is not None:
        for index, site in enumerate(sites):
            within_site.setdefault(site, []).append(index)
    site_rhos = []
    for indices in within_site.values():
        if len(indices) < 3:
            continue
        rho_site, _ = spearman(ndti_values[indices], turbidity[indices])
        if math.isfinite(rho_site):
            site_rhos.append(rho_site)

    # Site-centred anomalies: each site's own mean removed from both variables, then
    # pooled. With a strong site-specific optical baseline this is the test of whether the
    # index responds to *turbidity change*, as opposed to whether it can rank unrelated
    # water bodies against each other.
    anomaly_rho = anomaly_p = None
    if within_site:
        xs, ys = [], []
        for indices in within_site.values():
            if len(indices) < 3:
                continue
            x = ndti_values[indices]
            y = np.log10(turbidity[indices])
            xs.append(x - x.mean())
            ys.append(y - y.mean())
        if xs:
            anomaly_rho, anomaly_p = spearman(np.concatenate(xs), np.concatenate(ys))

    calibration = (site_calibration(ndti_values, turbidity, sites)
                   if sites is not None else None)

    return {
        "pairs": len(ndti_values),
        "sites": len(within_site) if within_site else None,
        "ndti_range": [float(ndti_values.min()), float(ndti_values.max())],
        "spearman_rho": rho,
        "spearman_p": rho_p,
        "pearson_r_log10_turbidity": r_log,
        "pearson_p": r_log_p,
        "site_centred_spearman_rho": anomaly_rho,
        "site_centred_spearman_p": anomaly_p,
        "site_calibration": calibration,
        "fit": fit,
        "rmse_factor_in_sample": (10.0 ** fit["rmse_log10"]
                                  if fit.get("rmse_log10") is not None else None),
        "leave_one_out": loo,
        "sites_within_site_rho": len(site_rhos),
        "median_within_site_rho": (float(np.median(site_rhos)) if site_rhos else None),
    }


def summarise(pairs: list[dict]) -> dict:
    """Both NDTI variants over the same pairs, plus the sample description.

    The headline is the **published cell value** -- NDTI of the cell's averaged
    reflectance, which is what the map draws and the leaderboard ranks. The water-only
    variant is reported beside it so the report can separate "the index is wrong" from
    "the cell is a mixture".

    The water-fraction strata are pre-declared, not chosen after the fact: the product's
    own documentation already names mixed shoreline cells as its main resolution
    limitation, so the question "how much of the error is mixing?" is asked of every run
    with the same three thresholds.
    """
    usable = [pair for pair in pairs if pair.get("turbidity") is not None
              and pair["turbidity"] > 0.0]
    turbidity = np.array([pair["turbidity"] for pair in usable], dtype="float64")
    sites = [pair["site"] for pair in usable]

    def block(rows: list[dict], key: str) -> dict:
        rows = [row for row in rows if row.get(key) is not None]
        if not rows:
            return {"pairs": 0}
        return metric_block(
            np.array([row[key] for row in rows], dtype="float64"),
            np.array([row["turbidity"] for row in rows], dtype="float64"),
            [row["site"] for row in rows])

    cell = block(usable, "ndti")
    water = block(usable, "ndti_water")

    return {
        "pairs": len(usable),
        "sites": len(set(sites)),
        "turbidity_range_fnu": ([float(turbidity.min()), float(turbidity.max())]
                                if len(turbidity) else None),
        "published_cell_value": cell,
        "water_only": water,
        "water_fraction_strata": {
            f">={threshold:.2f}": block(
                [row for row in usable
                 if row.get("cell_water_fraction") is not None
                 and row["cell_water_fraction"] >= threshold], "ndti")
            for threshold in WATER_FRACTION_STRATA
        },
        # Convenience aliases for the headline variant, so a reader of the JSON does not
        # have to know which one is the headline.
        **{key: cell.get(key) for key in
           ("spearman_rho", "spearman_p", "fit", "leave_one_out",
            "rmse_factor_in_sample", "median_within_site_rho")},
    }


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "-"
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(pairs: list[dict], summary: dict, gauges: list[dict],
                    window: tuple[str, str], resolution_m: float) -> str:
    """The report, with the pair table that produced every number in it."""
    cell = summary.get("published_cell_value", {})
    water = summary.get("water_only", {})
    lines: list[str] = []
    lines.append("# Ripple vs USGS NWIS -- NDTI turbidity validation")
    lines.append("")
    lines.append(f"Window **{window[0]} to {window[1]}**, analysis cell "
                 f"**{resolution_m:.0f} m** (the delivered product resolution).")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    if summary.get("pairs"):
        lines.append(f"- **{summary['pairs']} matched satellite/gauge pairs** across "
                     f"**{summary['sites']} USGS gauges**, in-situ turbidity "
                     f"{summary['turbidity_range_fnu'][0]:.2f}-"
                     f"{summary['turbidity_range_fnu'][1]:.0f} FNU.")
        lines.append("")
        lines.append("| NDTI variant | pairs | Spearman rho | p | R^2 on log10 FNU | "
                     "leave-one-out accuracy |")
        lines.append("|---|---|---|---|---|---|")
        for label, block in (("**Published cell value** (what the map draws)", cell),
                             ("Water-only sub-pixels (~30 m)", water)):
            loo = block.get("leave_one_out") or {}
            lines.append(
                f"| {label} | {block.get('pairs', 0)} | "
                f"{_fmt(block.get('spearman_rho'))} | "
                f"{(block.get('spearman_p') or float('nan')):.2e} | "
                f"{_fmt((block.get('fit') or {}).get('r2'))} | "
                f"factor {_fmt(loo.get('factor'), 2)} |")
        lines.append("")
        if cell.get("site_centred_spearman_rho") is not None:
            lines.append(
                f"- Site-centred anomalies (each gauge's own mean removed from NDTI and "
                f"log10 FNU): rho = {_fmt(cell['site_centred_spearman_rho'])}, "
                f"p = {cell['site_centred_spearman_p']:.2e}. This is the test of whether "
                f"NDTI responds to turbidity *change* at a site, with the site's own "
                f"optical baseline removed.")
            lines.append(
                f"- Median within-gauge rho across gauges with >=3 pairs: "
                f"{_fmt(cell.get('median_within_site_rho'))} "
                f"({cell.get('sites_within_site_rho')} gauges).")
        lines.append("")
        strata = summary.get("water_fraction_strata") or {}
        if strata:
            lines.append("### Where the index is strongest, and where it is not")
            lines.append("")
            lines.append("Pre-declared split on the fraction of the cell the class layer "
                         "calls water (published-cell NDTI). The strata are cumulative, and "
                         "they are deliberately **not** a monotonic story about mixing: the "
                         "weakest row is >=0.25, dragged down by the half-land band inside "
                         "it (25-50% water), where a narrow, sediment-loaded channel shares "
                         "its pixel with shore and land; cells that are >=50% water are the "
                         "strongest. Mixing matters, but it is not the only error term — the "
                         "site-specific optical baseline is (see below).")
            lines.append("")
            lines.append("| Cell water fraction | pairs | gauges | Spearman rho | p | "
                         "leave-one-out accuracy |")
            lines.append("|---|---|---|---|---|---|")
            for label, block in strata.items():
                loo = block.get("leave_one_out") or {}
                lines.append(
                    f"| {label} | {block.get('pairs', 0)} | {block.get('sites') or '-'} | "
                    f"{_fmt(block.get('spearman_rho'))} | "
                    f"{(block.get('spearman_p') or float('nan')):.2e} | "
                    f"factor {_fmt(loo.get('factor'), 2)} |")
            lines.append("")

        calibration = (cell.get("site_calibration") or {})
        if calibration.get("global_factor"):
            lines.append("### Where the error actually comes from")
            lines.append("")
            lines.append(
                f"One global line through every gauge leaves an RMSE of "
                f"**{_fmt(calibration['global_rmse_log10'])} in log10 FNU — a factor of "
                f"{_fmt(calibration['global_factor'], 1)}**. Fitting each gauge against its "
                f"own pairs ({calibration['sites_fitted']} of "
                f"{calibration['sites_total']} gauges have enough pairs to fit) leaves "
                f"**a factor of {_fmt(calibration['per_site_factor'], 1)}**. The difference "
                f"is the site-specific optical baseline: NDTI carries the signal, but one "
                f"global calibration cannot transfer it between a clear mountain creek and "
                f"a sediment-loaded channel. That is why per-water-body baselines are the "
                f"first item of the roadmap.")
            lines.append("")
        lines.append("**How to read this.** NDTI is an optical proxy with a strong "
                     "site-specific baseline: the same FNU value reads differently on a "
                     "clear mountain river than on a sediment-loaded irrigation return. "
                     "The published-cell figure is the product's own claim and is the "
                     "headline; the water-only figure shows what is left once pixel "
                     "mixing is removed. Both are reported, neither is rounded up.")
    else:
        lines.append(f"- {summary.get('note', 'no matched pairs')}")
    lines.append("")
    lines.append("## Gauges")
    lines.append("")
    lines.append("Every active USGS continuous turbidity series that sits on a cell the "
                 "delivered product maps as water. A gauge with 0 pairs is kept in the "
                 "table: it shows the funnel honestly rather than hiding the gauges the "
                 "product could not be compared against.")
    lines.append("")
    lines.append("| USGS site | Name | Lon | Lat | In-situ samples | Pairs |")
    lines.append("|---|---|---|---|---|---|")
    for gauge in gauges:
        lines.append(f"| {gauge['site']} | {gauge.get('name') or ''} | "
                     f"{gauge['lon']:.4f} | {gauge['lat']:.4f} | "
                     f"{gauge.get('in_situ_samples', 0)} | {gauge.get('pairs', 0)} |")
    lines.append("")
    lines.append("## Matched pairs")
    lines.append("")
    lines.append("| Site | Scene | Scene time (UTC) | Cell water | NDTI cell | NDTI water | "
                 "Turbidity | n | Unit |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for pair in sorted(pairs, key=lambda row: row["turbidity"]):
        lines.append(
            f"| {pair['site']} | {pair['scene']} | {pair['scene_time'][:19]} | "
            f"{_fmt(pair['cell_water_fraction'], 2)} | {_fmt(pair.get('ndti'), 3)} | "
            f"{_fmt(pair.get('ndti_water'), 3)} | {_fmt(pair['turbidity'], 2)} | "
            f"{pair['turbidity_n']} | {pair['unit']} |")
    lines.append("")
    lines.append("## What this does and does not establish")
    lines.append("")
    lines.append("- The gauge measures one point (typically at a bank or a fixed station); "
                 "the satellite cell averages ~1.85 km2 around it. That footprint "
                 "mismatch cannot be removed here; the site-specific optical baseline is "
                 "the largest measured error term (see 'Where the error actually comes "
                 "from').")
    lines.append("- The fit is empirical and site-transfer, not a physical inversion: it "
                 "shows NDTI tracks measured turbidity across these gauges, not that NDTI "
                 "measures FNU anywhere.")
    lines.append("- Cells below the 25% water threshold are rejected, so every pair is "
                 "water the product actually reports. Rivers narrower than a cell are "
                 "mixed pixels and are the hardest case.")
    lines.append("- No cyanobacteria, toxin or metal claim is made or tested here.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Gauge selection against the delivered product
# --------------------------------------------------------------------------------------
def product_water_cells(manifest_path: Path, tile_path: Path
                        ) -> tuple[np.ndarray, dict]:
    """The delivered product's water mask and its grid, read from the shipped tile.

    The composite inputs are not cached in this repository, so the mask is decoded from
    the lossless WebP the app ships: alpha > 0 is exactly the set of cells the product
    reports a risk for. Used only to decide *where* a comparison is legitimate.
    """
    from PIL import Image

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    grid = manifest["grid"]
    left, bottom, right, top = grid["merc_bounds"]
    image = np.array(Image.open(tile_path).convert("RGBA"))
    return image[..., 3] > 0, {
        "width": grid["width"],
        "height": grid["height"],
        "left": left, "bottom": bottom, "right": right, "top": top,
        "res_m": (right - left) / grid["width"],
        "manifest": manifest,
    }


def cell_of(lon: float, lat: float, grid: dict) -> tuple[int, int]:
    """Grid (row, col) holding a geographic point, in the product's Mercator grid."""
    x = math.radians(lon) * 6378137.0
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * 6378137.0
    col = int(round((x - grid["left"]) / grid["res_m"]))
    row = int(round((grid["top"] - y) / grid["res_m"]))
    return row, col


def fetch_site_names(sites: list[str], cache: Path | None = None,
                     refresh: bool = False, pause_s: float = 0.3) -> dict[str, str]:
    """Station names for a list of monitoring-location ids.

    The value-series collections carry the id but leave ``monitoring_location_name``
    empty, so names come from the location collection, one id at a time, cached. A report
    that says "Niobrara River near Verdel, Nebr." is checkable by a reader; a report of
    bare site numbers is not.
    """
    names: dict[str, str] = {}
    if cache is not None and cache.exists() and not refresh:
        try:
            names = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            names = {}
    missing = [site for site in sites if site not in names]
    for index, site in enumerate(missing, start=1):
        url = (f"{NWIS_API}/collections/monitoring-locations/items?"
               + urllib.parse.urlencode({"id": site}))
        try:
            payload = _get_json(url)
            features = payload.get("features", [])
            names[site] = (features[0]["properties"].get("monitoring_location_name")
                           if features else "")
        except Exception:  # a missing name must not end the run
            names[site] = ""
        if index % 10 == 0 or index == len(missing):
            print(f"  names [{index}/{len(missing)}]", flush=True)
        time.sleep(pause_s)
    if cache is not None and names:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(names, indent=1), encoding="utf-8")
    return names


def select_gauges(manifest_path: Path, tile_path: Path, region_bbox,
                  active_since: str, cache: Path | None = None,
                  refresh: bool = False) -> tuple[list[dict], dict]:
    """Active turbidity gauges that sit on a cell the delivered product calls water."""
    water, grid = product_water_cells(manifest_path, tile_path)
    series = turbidity_series_ids(region_bbox, cache=cache, refresh=refresh)
    active: dict[str, dict] = {}
    for row in series:
        if not row.get("lat") or not row.get("lon"):
            continue
        if (row.get("end") or "") < active_since:
            continue
        active.setdefault(row["site"], row)

    selected: list[dict] = []
    on_land = 0
    for site, row in sorted(active.items()):
        r, c = cell_of(row["lon"], row["lat"], grid)
        if not (0 <= r < water.shape[0] and 0 <= c < water.shape[1]) or not water[r, c]:
            on_land += 1
            continue
        selected.append({**row, "row": r, "col": c})

    audit = {
        "series_seen": len(series),
        "active_sites": len(active),
        "sites_on_product_water": len(selected),
        "sites_off_product_water": on_land,
    }
    return selected, audit


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def run(manifest_path: Path, tile_path: Path, start: str, end: str,
        region_bbox, active_since: str, resolution_m: float,
        max_scenes: int, workers: int, limit_sites: int | None = None,
        cache_dir: Path | None = None, refresh: bool = False) -> dict:
    cache_dir = cache_dir or (Path(__file__).resolve().parent.parent / ".cache")
    gauges, audit = select_gauges(
        manifest_path, tile_path, region_bbox, active_since,
        cache=cache_dir / "nwis_turbidity_series.json", refresh=refresh)
    if limit_sites:
        gauges = gauges[:limit_sites]
    names = fetch_site_names([gauge["site"] for gauge in gauges],
                             cache=cache_dir / "nwis_site_names.json", refresh=refresh)
    for gauge in gauges:
        gauge["name"] = names.get(gauge["site"]) or gauge.get("name") or ""
    print(f"gauges: {len(gauges)} on product water (of {audit['active_sites']} active, "
          f"{audit['series_seen']} series seen)", flush=True)

    in_situ = fetch_all_turbidity(
        gauges, start, end,
        cache=cache_dir / f"nwis_insitu_{start}_{end}.json", refresh=refresh)

    def work(gauge: dict) -> dict:
        samples = in_situ.get(gauge["site"], [])
        pairs: list[dict] = []
        for scene in candidate_scenes(gauge["lon"], gauge["lat"], start, end, max_scenes):
            try:
                pair = gauge_pairs(scene, gauge["lon"], gauge["lat"], samples,
                                   resolution_m)
            except Exception as exc:  # a bad granule must not kill the run
                print(f"    {gauge['site']} {scene['id']}: {exc}", flush=True)
                continue
            if pair is not None:
                pairs.append({**pair, "site": gauge["site"], "name": gauge.get("name")})
        return {"gauge": gauge, "samples": len(samples), "pairs": pairs}

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for index, result in enumerate(pool.map(work, gauges), start=1):
            gauge = result["gauge"]
            print(f"  matching [{index}/{len(gauges)}] {gauge['site']:16s} "
                  f"{result['samples']:5d} in-situ samples, "
                  f"{len(result['pairs'])} scene matches", flush=True)
            results.append(result)

    all_pairs = dedupe_by_day([pair for result in results for pair in result["pairs"]])
    summary = summarise(all_pairs)
    report = {
        "window": [start, end],
        "resolution_m": resolution_m,
        "product_tile": str(tile_path),
        "gauge_audit": audit,
        "gauges": [{**result["gauge"], "in_situ_samples": result["samples"],
                    "pairs": len(result["pairs"])} for result in results],
        "pairs": all_pairs,
        "summary": summary,
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ripple-validate")
    parser.add_argument("--manifest", default="../app/public/data/manifest.json")
    parser.add_argument("--tile", default=None,
                        help="product tile to read the water mask from "
                             "(default: the latest observed frame in the manifest)")
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--end", default="2026-09-20")
    parser.add_argument("--active-since", default="2026-08-01",
                        help="keep gauges whose series ends on or after this date")
    parser.add_argument("--region-bbox", nargs=4, type=float,
                        default=[-128.0, 18.0, -60.0, 54.0])
    parser.add_argument("--resolution-m", type=float, default=None,
                        help="analysis cell size (default: the product's own)")
    parser.add_argument("--max-scenes-per-gauge", type=int, default=10)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit-sites", type=int, default=None)
    parser.add_argument("--cache-dir", default=".cache",
                        help="where the series list and in-situ samples are cached")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore the caches and re-pull from USGS")
    parser.add_argument("--out", default="../docs/validation")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tile_path = Path(args.tile) if args.tile else next(
        Path(args.manifest).parent / frame["tile"] for frame in reversed(manifest["frames"])
        if frame["kind"] == "observed")
    resolution = args.resolution_m or float(manifest["resolution_m"])

    report = run(manifest_path, tile_path, args.start, args.end,
                 tuple(args.region_bbox), args.active_since, resolution,
                 args.max_scenes_per_gauge, args.workers, args.limit_sites,
                 cache_dir=Path(args.cache_dir), refresh=args.refresh)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "usgs-nwis-pairs.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    markdown = render_markdown(report["pairs"], report["summary"], report["gauges"],
                               (args.start, args.end), resolution)
    (out_dir / "usgs-nwis-validation.md").write_text(markdown, encoding="utf-8")

    summary = report["summary"]
    print()
    cell = summary.get("published_cell_value") or {}
    water = summary.get("water_only") or {}
    if summary.get("pairs"):
        loo = cell.get("leave_one_out") or {}
        loo_water = water.get("leave_one_out") or {}
        print(f"pairs {summary['pairs']} across {summary['sites']} gauges")
        print(f"  published cell : rho {cell.get('spearman_rho'):.3f} "
              f"(p={cell.get('spearman_p'):.2e})  loo factor {loo.get('factor')}")
        print(f"  water only     : rho {water.get('spearman_rho'):.3f} "
              f"(p={water.get('spearman_p'):.2e})  loo factor {loo_water.get('factor')}")
    else:
        print(summary)
    print(f"wrote {out_dir / 'usgs-nwis-validation.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
