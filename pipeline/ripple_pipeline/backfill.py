"""Recompute per-body statistics for an already-delivered manifest.

Why this exists: the body statistics in the shipped ``manifest.json`` were produced by a
point sample -- one cell per body -- and the fix (an areal mean over each body's footprint,
see ``waters.footprint_statistics``) needs the frame rasters, which are not cached in this
repository. Re-running the pipeline to regenerate them means ~7,000 remote scene reads and
about 1.5 hours, and it would also replace the frames the documentation's screenshots were
taken from.

So the statistics are recomputed from the *delivered product raster* instead. The frames
ship as lossless WebP where alpha > 0 is exactly the set of cells the product reports a
risk for and the colour is a linear interpolation of risk -- both invertible:

* the water mask decodes exactly (alpha is 0 or 190, no intermediate values);
* risk decodes to ~0.001, the ramp's own 8-bit resolution (measured below and reported by
  ``verify_round_trip``);
* body footprints are re-derived by running the same ``discover_bodies`` sieve on that
  mask with the same thresholds, which is deterministic, so the body ids must match the
  manifest exactly -- and this script refuses to write anything if they do not.

What cannot be recovered from the raster is anything not encoded in it: per-cell water
fraction and the NDCI/NDTI decomposition (the tile stores the composite risk only). Those
fields are left as they were, or nulled where their meaning changed, and the manifest
carries a note saying so.

Run:  python -m ripple_pipeline.backfill --manifest ../app/public/data/manifest.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config
from .palette import PALETTE_STOPS
from .runoff import load_risk
from .waters import CATALOG, body_labels, discover_bodies, footprint_statistics

CURVE_SAMPLES = 65536


def ramp_curve() -> tuple[np.ndarray, np.ndarray]:
    """The palette as a dense table: (risk, RGB) with risk on a regular grid."""
    stops = np.array([stop for stop, _ in PALETTE_STOPS], dtype="float64")
    colours = np.array([colour for _, colour in PALETTE_STOPS], dtype="float64")
    risk = np.linspace(0.0, 1.0, CURVE_SAMPLES)
    rgb = np.stack([np.interp(risk, stops, colours[:, channel]) for channel in range(3)],
                   axis=-1)
    return risk, rgb


def invert_ramp(rgb: np.ndarray) -> np.ndarray:
    """Risk for each RGB triple, by projecting onto the ramp curve.

    The ramp is a monotone 1-D curve through RGB space, so the projection is unique. The
    palette has ~550 distinct colours in a delivered frame, so this is done per unique
    colour rather than per pixel. Accepts ``(..., 3)`` and returns ``(...)``.
    """
    risk, curve = ramp_curve()
    flat = rgb.reshape(-1, 3).astype("float64")
    unique, inverse = np.unique(flat, axis=0, return_inverse=True)

    out = np.empty(len(unique), dtype="float64")
    step = 256
    for start in range(0, len(unique), step):
        chunk = unique[start:start + step]
        distance = np.linalg.norm(chunk[:, None, :] - curve[None, :, :], axis=2)
        out[start:start + step] = risk[np.argmin(distance, axis=1)]
    return out[inverse].reshape(rgb.shape[:-1])


def decode_frame(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(risk, water mask) from a delivered tile. Risk is NaN outside the mask."""
    from PIL import Image

    image = np.array(Image.open(path).convert("RGBA"))
    water = image[..., 3] > 0
    risk = np.full(image.shape[:2], np.nan, dtype="float32")
    if water.any():
        risk[water] = invert_ramp(image[..., :3][water]).astype("float32")
    return risk, water


def verify_round_trip(tile_path: Path) -> dict:
    """How much information the palette costs, measured on a real tile.

    Reported rather than asserted: the number is small (~0.001) but it is not zero, and a
    reader of the backfilled manifest is entitled to know its precision.
    """
    from PIL import Image

    image = np.array(Image.open(tile_path).convert("RGBA"))
    water = image[..., 3] > 0
    if not water.any():
        return {"cells": 0}
    decoded = invert_ramp(image[..., :3][water])
    _, curve = ramp_curve()
    rebuilt = np.stack([np.interp(decoded, np.linspace(0, 1, CURVE_SAMPLES),
                                  curve[:, channel]) for channel in range(3)], axis=-1)
    error = np.linalg.norm(rebuilt - image[..., :3][water].astype("float64"), axis=1)
    return {
        "cells": int(water.sum()),
        "unique_colours": int(len(np.unique(image[..., :3][water], axis=0))),
        "max_rgb_error": round(float(error.max()), 3),
        "mean_rgb_error": round(float(error.mean()), 3),
        "risk_resolution": round(1.0 / CURVE_SAMPLES, 6),
    }


def build_grid(manifest: dict) -> dict:
    """Rebuild the pipeline's grid exactly.

    The resolution comes from ``manifest["resolution_m"]`` and NOT from
    ``(right - left) / width``: the two differ by ~0.006 m per cell, which accumulates to
    ~24 m across the grid. Body ids are built from a centroid rounded to two decimals of
    latitude, so a 24 m shift renames a body that sits on a rounding boundary -- which is
    how this was discovered (``wm85p31_36p58`` vs ``wm85p31_36p59``).
    """
    from affine import Affine

    grid = manifest["grid"]
    left, bottom, right, top = grid["merc_bounds"]
    res_m = float(manifest["resolution_m"])
    return {
        "crs": grid["crs"],
        "width": grid["width"],
        "height": grid["height"],
        "res_m": res_m,
        "transform": Affine(res_m, 0.0, left, 0.0, -res_m, top),
        "merc_bounds": tuple(grid["merc_bounds"]),
        "bbox": tuple(manifest["region_bbox"]),
    }


def frame_stats(risk: np.ndarray, labels: np.ndarray, footprint: np.ndarray,
                count: int) -> list[dict]:
    return footprint_statistics(labels, risk, np.isfinite(risk), count)


def backfill(manifest_path: Path, data_dir: Path, dry_run: bool = False) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grid = build_grid(manifest)
    discovery = manifest.get("discovery", {})

    observed = [frame for frame in manifest["frames"] if frame["kind"] == "observed"]
    if not observed:
        raise RuntimeError("manifest has no observed frames")
    latest = observed[-1]

    latest_risk, water = decode_frame(data_dir / latest["tile"])
    round_trip = verify_round_trip(data_dir / latest["tile"])
    print(f"decoded {latest['id']}: {int(water.sum())} water cells, "
          f"max RGB error {round_trip['max_rgb_error']} "
          f"({round_trip['unique_colours']} distinct colours)", flush=True)

    bodies = discover_bodies(
        water, grid,
        min_area_km2=float(discovery.get("min_body_km2", config.BODY_MIN_AREA_KM2)),
        max_bodies=int(discovery.get("max_bodies", config.BODY_MAX_COUNT)),
        curated=CATALOG, match_km=config.BODY_NAME_MATCH_KM)

    manifest_ids = [body["id"] for body in manifest["water_bodies"]]
    discovered_ids = [body.id for body in bodies]
    if manifest_ids != discovered_ids:
        missing = sorted(set(manifest_ids) - set(discovered_ids))
        extra = sorted(set(discovered_ids) - set(manifest_ids))
        raise RuntimeError(
            "discovery from the delivered raster does not reproduce the manifest's "
            f"bodies: {len(missing)} missing ({missing[:5]}), {len(extra)} extra "
            f"({extra[:5]}). Refusing to write a partial match.")
    print(f"footprints reproduce all {len(bodies)} bodies by id", flush=True)

    labels, footprint = body_labels(bodies, grid)
    measured = int((footprint > 0).sum())
    sizes = footprint[footprint > 0]
    print(f"footprints: {measured} bodies, median {int(np.median(sizes))} cells, "
          f"largest {int(sizes.max())} cells", flush=True)

    stats_by_frame: dict[str, list[dict]] = {}
    for frame in manifest["frames"]:
        risk, _ = decode_frame(data_dir / frame["tile"])
        stats = frame_stats(risk, labels, footprint, len(bodies))
        stats_by_frame[frame["id"]] = stats
        measured = sum(1 for stat in stats if stat["mean"] is not None)
        print(f"  {frame['id']}: {measured} bodies with a reading", flush=True)

    for position, (entry, discovered) in enumerate(
            zip(manifest["water_bodies"], bodies)):
        entry = dict(entry)
        # Properties that live in the catalog, not in the delivered raster, come from the
        # discovery pass: the caution note is new with this schema and has no source in
        # the manifest being rewritten.
        entry["caution"] = discovered.caution
        for sample in entry["series"]:
            stat = stats_by_frame[sample["frame"]][position]
            sample["risk"] = stat["mean"]
            sample["risk_peak"] = stat["peak"]
            sample["risk_std"] = stat["std"]
            sample["cells"] = stat["cells"]
            sample["cell_fraction"] = (round(stat["cells"] / footprint[position], 3)
                                       if footprint[position] > 0 else None)
            sample["valid"] = stat["mean"] is not None
            # The tile carries the composite risk only: per-cell water fraction and the
            # NDCI/NDTI decomposition are not recoverable from it.
            sample["water_fraction"] = None
            sample.pop("offset_cells", None)
        latest_sample = next((sample for sample in reversed(entry["series"])
                              if sample["frame"].startswith("obs")), None)
        entry["latest_risk"] = latest_sample["risk"] if latest_sample else None
        entry["latest_peak"] = latest_sample.get("risk_peak") if latest_sample else None
        entry["latest_std"] = latest_sample.get("risk_std") if latest_sample else None
        entry["latest_cells"] = latest_sample.get("cells") if latest_sample else None
        entry["latest_cell_fraction"] = (latest_sample.get("cell_fraction")
                                         if latest_sample else None)
        entry["observed_frames"] = sum(
            1 for sample in entry["series"]
            if sample["frame"].startswith("obs") and sample.get("valid"))
        if entry.get("forecast") and entry["latest_risk"] is not None:
            entry["forecast"] = rebuild_forecast(entry["forecast"], entry["latest_risk"])
        manifest["water_bodies"][position] = entry

    manifest["schema_version"] = 3
    manifest["body_statistic"] = (
        "per-body risk is the areal mean over the body's own footprint; risk_peak is the "
        "worst single cell in it. A point sample at one cell was the previous behaviour "
        "and is what the peak column preserves.")
    manifest["body_stats_recomputed_utc"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    manifest["body_stats_method"] = (
        "Per-body statistics were recomputed from the delivered lossless tiles with "
        "ripple_pipeline.backfill: the composite inputs are not cached in this "
        "repository. Risk decodes to ~0.001 (the ramp's 8-bit resolution); per-cell water "
        "fraction and the NDCI/NDTI decomposition are not encoded in the tile and are "
        "null for this build.")

    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "bodies": len(bodies),
        "round_trip": round_trip,
        "top_by_mean": sorted(
            [(body["name"], body["latest_risk"], body.get("latest_peak"),
              body.get("latest_cells"), body.get("latest_cell_fraction"))
             for body in manifest["water_bodies"]
             if body.get("latest_risk") is not None],
            key=lambda row: -row[1])[:15],
    }


def rebuild_forecast(forecast: dict, baseline_risk: float) -> dict:
    """Re-express a body's forecast on its new baseline.

    The forecast is ``baseline + anomaly``, and the anomaly is a function of the runoff
    already recorded in the manifest, so the only thing that changes is the baseline.
    Recomputed with the pipeline's own ``load_risk`` rather than by scaling the old curve.
    """
    land_cover = forecast.get("land_cover", "row_crop")
    anomaly_divisor = forecast.get("anomaly_divisor")
    if anomaly_divisor is None:
        from .forecast_field import ANOMALY_DIVISOR
        anomaly_divisor = ANOMALY_DIVISOR
    rebuilt = dict(forecast)
    rebuilt["baseline_risk"] = round(float(baseline_risk), 4)
    rebuilt["risk"] = [
        round(min(1.0, baseline_risk + min(1.0, load_risk(runoff, land_cover)
                                            / anomaly_divisor)), 4)
        for runoff in forecast.get("runoff_mm", [])]
    return rebuilt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ripple-backfill")
    parser.add_argument("--manifest", default="../app/public/data/manifest.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    report = backfill(manifest_path, manifest_path.parent, dry_run=args.dry_run)
    print()
    print("top bodies by areal mean (name, mean, peak, cells, cell fraction):")
    for row in report["top_by_mean"]:
        print(f"   {row[0][:44]:46s} mean {row[1]:.3f}  peak {row[2]:.3f}  "
              f"cells {row[3]}  coverage {row[4]}")
    print()
    print(json.dumps(report["round_trip"], indent=1))
    if args.dry_run:
        print("dry run: nothing written")
    else:
        print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
