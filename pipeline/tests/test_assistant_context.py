import json
from pathlib import Path

import pytest

from ripple_pipeline.assistant_context import LIMITS, build

MANIFEST = Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "manifest.json"
VALIDATION = (Path(__file__).resolve().parents[2] / "docs" / "validation"
              / "usgs-nwis-pairs.json")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def context(manifest):
    return build(MANIFEST, VALIDATION)


def test_every_catalogued_body_becomes_a_record(manifest, context):
    assert context["dataset"]["body_count"] == len(manifest["water_bodies"])
    assert len(context["bodies"]) == len(manifest["water_bodies"])
    ids = [body["id"] for body in context["bodies"]]
    assert ids == [body["id"] for body in manifest["water_bodies"]]


def test_a_body_carries_the_manifest_numbers_unchanged(manifest, context):
    source = next(body for body in manifest["water_bodies"] if body["id"] == "lake-erie")
    record = next(body for body in context["bodies"] if body["id"] == "lake-erie")
    assert record["latest"]["risk"] == source["latest_risk"]
    assert record["latest"]["cells"] == source["latest_cells"]
    assert record["latest"]["risk_peak"] == source["latest_peak"]
    assert len(record["observed"]) == sum(
        1 for sample in source["series"] if sample["frame"].startswith("obs"))
    assert record["forecast"]["baseline_risk"] == source["forecast"]["baseline_risk"]
    assert record["forecast"]["runoff_mm"] == source["forecast"]["runoff_mm"]


def test_observed_records_carry_their_frame_dates(manifest, context):
    frames = {frame["id"]: frame for frame in manifest["frames"]}
    for body in context["bodies"]:
        for sample in body["observed"]:
            assert sample["date"] == frames[sample["frame"]]["date"]
            assert sample["frame"].startswith("obs-")


def test_nothing_is_derived_that_the_manifest_does_not_state(manifest, context):
    """The generator copies; it must not invent a statistic of its own."""
    record = next(body for body in context["bodies"] if body["id"] == "lake-erie")
    assert set(record) == {
        "id", "name", "kind", "area_km2", "curated", "lat", "lon", "caution",
        "observed", "latest", "forecast"}
    assert set(record["latest"]) == {"frame", "date", "risk", "risk_peak", "cells", "valid"}


def test_the_method_block_carries_the_datasets_own_words(manifest, context):
    assert context["method"]["disclaimer"] == manifest["disclaimer"]
    assert context["method"]["forecast_basis"] == manifest["forecast"]["basis"]
    assert context["method"]["forecast_dates"] == manifest["forecast"]["dates"]
    assert len(context["method"]["limits"]) == len(LIMITS)
    assert any("cyanobacteria" in limit for limit in context["method"]["limits"])


def test_the_validation_summary_matches_the_report_when_present(context):
    validation = context.get("validation")
    if validation is None:
        pytest.skip("validation report not shipped next to the manifest")
    assert validation["pairs"] > 0
    assert validation["gauges"] > 0
    assert 0.0 <= validation["published_cell_value"]["spearman_rho"] <= 1.0
    assert validation["water_only"]["spearman_rho"] >= 0.0
    for label, block in validation["strata"].items():
        assert label.startswith(">=")
        assert block["pairs"] >= 0
        assert block["spearman_rho"] is None or -1.0 <= block["spearman_rho"] <= 1.0


def test_the_caution_travels_with_the_body(manifest, context):
    salt = next(body for body in context["bodies"] if body["id"] == "great-salt-lake")
    assert salt["caution"] and "hypersaline" in salt["caution"].lower()
    others = [body for body in context["bodies"] if body["id"] != "great-salt-lake"]
    assert all(body["caution"] is None for body in others)


def test_no_record_contains_a_non_finite_number(context):
    text = json.dumps(context)
    assert "NaN" not in text and "Infinity" not in text


def test_the_file_stays_small_enough_to_fetch_lazily(context, tmp_path):
    path = tmp_path / "assistant.json"
    path.write_text(json.dumps(context, indent=1), encoding="utf-8")
    assert path.stat().st_size < 700_000, "the fact base is getting too big to fetch lazily"
