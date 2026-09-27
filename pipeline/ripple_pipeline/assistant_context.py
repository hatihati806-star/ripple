"""Build the assistant's fact base from the shipped manifest and validation report.

The assistant must never answer from the model's own knowledge, so everything it is allowed
to say has to exist as a record somewhere in this file. This module is the only producer of
that file, and it is deliberately boring: it copies numbers out of the delivered manifest
(and the USGS validation summary) and adds nothing.

Rules it enforces:

* every body carries the frames and values it actually has, or an explicit ``null``;
* no derived quantity is invented here -- if a number is not in the manifest, it is absent;
* the method and limitation strings travel with the data, so the assistant can state the
  limits of its own dataset without paraphrasing them;
* the file stays small enough to fetch lazily in the browser (it is not part of first paint).

Run:  python -m ripple_pipeline.assistant_context
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


SCHEMA = 1

# The dataset's own limits, verbatim from the docs, so the assistant states them rather
# than composing its own version of them.
LIMITS = [
    "Optical proxies only: nothing here detects toxins, bacteria or metals directly.",
    "Never cyanobacteria and never toxicity. Discriminating cyanobacteria needs the "
    "phycocyanin feature near 620 nm, which Sentinel-2 does not sample. Bloom intensity "
    "only.",
    "The index is relative to this region's observed p05-p95 range: 0.5 means the middle "
    "of that range, not a threshold and not a health standard.",
    "Not for drinking or swimming decisions, and not a regulatory measurement.",
    "Resolution is ~1.85 km per cell. A 20 km2 body is about six cells. The largest "
    "measured source of error is the site-specific optical baseline, not pixel mixing; "
    "both are quantified in the validation report.",
    "The 7-day outlook is a relative rainfall-runoff loading anomaly, not a concentration "
    "forecast. There is no catchment routing, no travel time and no calibrated yield.",
    "Cloud is not pollution: grey means no cloud-free observation in that window.",
    "Hypersaline and mineral water is only partially detected; where the catalog carries a "
    "caution (Great Salt Lake), the turbidity index is not a sediment proxy.",
]

INDICES = {
    "NDCI": "Normalized Difference Chlorophyll Index = (red edge 1 - red) / (red edge 1 + "
            "red). Algal bloom intensity proxy (Mishra & Mishra 2012).",
    "NDTI": "Normalized Difference Turbidity Index = (red - green) / (red + green). "
            "Suspended sediment / turbidity proxy (Lacaux et al. 2007).",
    "risk": "Composite 0..1 risk = the worse of the two normalized indices. It is the "
            "colour the map draws, normalized against this region's observed range.",
}


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def body_record(body: dict, frames: dict[str, dict]) -> dict:
    """One body's records, copied from the manifest without interpretation."""
    series = body.get("series") or []
    observed = []
    for sample in series:
        frame = frames.get(sample.get("frame"))
        if not frame or frame.get("kind") != "observed":
            continue
        observed.append({
            "frame": sample["frame"],
            "date": frame.get("date"),
            "risk": _round(sample.get("risk")),
            "risk_peak": _round(sample.get("risk_peak")),
            "cells": sample.get("cells"),
            "valid": bool(sample.get("valid")),
        })

    latest = next((entry for entry in reversed(observed)), None)
    forecast = body.get("forecast")
    record = {
        "id": body.get("id"),
        "name": body.get("name"),
        "kind": body.get("kind"),
        "area_km2": body.get("area_km2"),
        "curated": bool(body.get("curated")),
        "lat": body.get("lat"),
        "lon": body.get("lon"),
        "caution": body.get("caution"),
        "observed": observed,
        "latest": latest,
        "forecast": None,
    }
    if forecast and latest:
        record["forecast"] = {
            "baseline_risk": _round(forecast.get("baseline_risk")),
            "antecedent_rain_mm": forecast.get("antecedent_rain_mm"),
            "curve_number": forecast.get("curve_number"),
            "land_cover": forecast.get("land_cover"),
            "soil_group": forecast.get("soil_group"),
            "rain_mm": forecast.get("rain_mm"),
            "runoff_mm": forecast.get("runoff_mm"),
            "risk": [_round(value) for value in forecast.get("risk", [])],
        }
    return record


def build(manifest_path: Path, validation_path: Path | None = None) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    frames = {frame["id"]: frame for frame in manifest["frames"]}

    bodies = [body_record(body, frames) for body in manifest.get("water_bodies", [])]
    measured = [body for body in bodies if body["latest"] and body["latest"]["valid"]]

    context = {
        "schema": SCHEMA,
        "generated_utc": manifest.get("generated_utc"),
        "dataset": {
            "name": "Ripple",
            "one_liner": "A weather-radar-style forecast map for lake and river water "
                         "quality.",
            "region_name": manifest.get("region_name"),
            "region_bbox": manifest.get("region_bbox"),
            "resolution_m": manifest.get("resolution_m"),
            "ramp_semantics": manifest.get("ramp_semantics"),
            "body_statistic": manifest.get("body_statistic"),
            "body_stats_method": manifest.get("body_stats_method"),
            "bloom_threshold_ndci": manifest.get("bloom_threshold_ndci"),
            "coverage": manifest.get("coverage"),
            "discovery": manifest.get("discovery"),
            "frames": [{
                "id": frame["id"],
                "kind": frame["kind"],
                "label": frame.get("label"),
                "date": frame.get("date"),
                "window_label": frame.get("window_label"),
                "forecast_day": frame.get("forecast_day"),
                "scene_count": frame.get("scene_count"),
                "age_days": frame.get("age_days"),
                "coverage": frame.get("coverage"),
                "anomaly_mean": frame.get("anomaly_mean"),
            } for frame in manifest["frames"]],
            "body_count": len(bodies),
            "bodies_with_a_reading": len(measured),
            "named_from_catalog": sum(1 for body in bodies if body["curated"]),
            "data_sources": [
                "Sentinel-2 L2A surface reflectance COGs via the Earth Search STAC API",
                "Open-Meteo forecast API for rainfall (forecast frames and point forecasts)",
                "USGS Water Data OGC API for in-situ turbidity validation",
                "Natural Earth ocean polygons, OpenFreeMap basemap, AWS/Mapzen terrain DEM",
            ],
            "key_free": True,
        },
        "method": {
            "indices": INDICES,
            "forecast_basis": (manifest.get("forecast") or {}).get("basis"),
            "forecast_dates": (manifest.get("forecast") or {}).get("dates"),
            "forecast_region_rain_mm": (manifest.get("forecast") or {}).get("region_rain_mm"),
            "forecast_assumptions": (manifest.get("forecast") or {}).get("assumptions"),
            "disclaimer": manifest.get("disclaimer"),
            "limits": LIMITS,
        },
        "bodies": bodies,
    }

    if validation_path and Path(validation_path).exists():
        report = json.loads(Path(validation_path).read_text(encoding="utf-8"))
        summary = report.get("summary", {})
        cell = summary.get("published_cell_value", {})
        water = summary.get("water_only", {})
        context["validation"] = {
            "what": "NDTI compared against USGS NWIS continuous in-situ turbidity "
                    "(parameter 63680) at gauges that sit on cells this product maps as "
                    "water.",
            "window": report.get("window"),
            "pairs": summary.get("pairs"),
            "gauges": summary.get("sites"),
            "turbidity_range_fnu": summary.get("turbidity_range_fnu"),
            "published_cell_value": {
                "spearman_rho": _round(cell.get("spearman_rho"), 3),
                "spearman_p": cell.get("spearman_p"),
                "leave_one_out_factor": (cell.get("leave_one_out") or {}).get("factor"),
                "pairs": cell.get("pairs"),
            },
            "water_only": {
                "spearman_rho": _round(water.get("spearman_rho"), 3),
                "spearman_p": water.get("spearman_p"),
                "leave_one_out_factor": (water.get("leave_one_out") or {}).get("factor"),
                "pairs": water.get("pairs"),
            },
            "strata": {
                label: {
                    "pairs": block.get("pairs"),
                    "sites": block.get("sites"),
                    "spearman_rho": _round(block.get("spearman_rho"), 3),
                    "leave_one_out_factor": (block.get("leave_one_out") or {}).get("factor"),
                }
                for label, block in (summary.get("water_fraction_strata") or {}).items()
            },
            "honest_limits": [
                "A gauge measures one point, usually at a bank; a cell averages ~3.4 km2.",
                "The fit is empirical and site-transfer, not a physical inversion.",
                "Nothing in the validation tests toxins, bacteria, metals or cyanobacteria.",
            ],
            "source": "docs/validation/usgs-nwis-validation.md",
        }
    return context


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ripple-assistant-context")
    parser.add_argument("--manifest", default="../app/public/data/manifest.json")
    parser.add_argument("--validation", default="../docs/validation/usgs-nwis-pairs.json")
    parser.add_argument("--out", default="../app/public/data/assistant.json")
    args = parser.parse_args(argv)

    context = build(Path(args.manifest), Path(args.validation))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(context, indent=1), encoding="utf-8")

    size_kb = out.stat().st_size / 1024
    print(f"wrote {out} ({size_kb:.0f} KB)")
    print(f"  bodies {context['dataset']['body_count']} "
          f"({context['dataset']['bodies_with_a_reading']} with a reading, "
          f"{context['dataset']['named_from_catalog']} named)")
    print(f"  frames {len(context['dataset']['frames'])}, "
          f"validation {'present' if 'validation' in context else 'MISSING'}")
    print(f"  generated_utc {context['generated_utc']}")
    if size_kb > 600:
        print("  WARNING: larger than 600 KB, trim before shipping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
