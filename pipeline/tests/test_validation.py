import math
from datetime import datetime, timezone

import numpy as np
import pytest

from ripple_pipeline import validation as V

# ---------------------------------------------------------------------------------------
# Cell geometry
# ---------------------------------------------------------------------------------------
def test_cell_bbox_is_a_cell_wide():
    lon, lat, res = -98.0, 42.0, 1848.0
    west, south, east, north = V.cell_bbox(lon, lat, res)
    assert west < lon < east
    assert south < lat < north
    height_m = (north - south) * 110_574.0
    width_m = (east - west) * 111_320.0 * math.cos(math.radians(lat))
    assert abs(height_m - res) < 1.0
    assert abs(width_m - res) < 1.0


def test_cell_bbox_widens_longitude_at_high_latitude():
    narrow = V.cell_bbox(0.0, 5.0, 1848.0)
    wide = V.cell_bbox(0.0, 60.0, 1848.0)
    # cos(60) / cos(5) ~= 2.0, so a cell spans about twice the longitude at 60N.
    assert (wide[2] - wide[0]) > 1.8 * (narrow[2] - narrow[0])


def test_cell_bbox_survives_the_pole_guard():
    west, south, east, north = V.cell_bbox(0.0, 89.99, 1848.0)
    assert east > west and north > south


# ---------------------------------------------------------------------------------------
# In-situ matching
# ---------------------------------------------------------------------------------------
def _samples(*pairs):
    return [{"time": stamp, "value": value, "unit": "FNU"} for stamp, value in pairs]


def test_match_sample_takes_the_median_in_the_window():
    when = datetime(2026, 9, 16, 16, 53, tzinfo=timezone.utc)
    samples = _samples(
        ("2026-09-16T12:00:00+00:00", 5.0),     # outside +-3 h
        ("2026-09-16T15:30:00+00:00", 10.0),
        ("2026-09-16T16:45:00+00:00", 20.0),
        ("2026-09-16T17:15:00+00:00", 60.0),    # spike, must not set the value
        ("2026-09-16T21:00:00+00:00", 99.0),    # outside
    )
    match = V.match_sample(samples, when)
    assert match["value"] == 20.0
    assert match["n"] == 3
    assert match["max"] == 60.0


def test_match_sample_needs_at_least_two_points():
    when = datetime(2026, 9, 16, 16, 53, tzinfo=timezone.utc)
    samples = _samples(("2026-09-16T16:45:00+00:00", 20.0))
    assert V.match_sample(samples, when) is None


def test_match_sample_handles_a_naive_scene_time():
    samples = _samples(
        ("2026-09-16T16:45:00+00:00", 20.0),
        ("2026-09-16T17:00:00+00:00", 22.0),
    )
    assert V.match_sample(samples, datetime(2026, 9, 16, 16, 53))["n"] == 2


def test_match_sample_window_is_configurable():
    when = datetime(2026, 9, 16, 16, 53, tzinfo=timezone.utc)
    samples = _samples(
        ("2026-09-16T13:00:00+00:00", 1.0),
        ("2026-09-16T13:15:00+00:00", 2.0),
        ("2026-09-16T16:45:00+00:00", 20.0),
    )
    assert V.match_sample(samples, when, window_hours=4.0)["value"] == 2.0
    assert V.match_sample(samples, when, window_hours=0.5) is None


def test_match_sample_keeps_a_zero_reading():
    """0.0 FNU is a measurement of clear water, not a missing value."""
    when = datetime(2026, 9, 16, 16, 53, tzinfo=timezone.utc)
    samples = _samples(
        ("2026-09-16T16:45:00+00:00", 0.0),
        ("2026-09-16T17:00:00+00:00", 0.0),
    )
    assert V.match_sample(samples, when)["value"] == 0.0


# ---------------------------------------------------------------------------------------
# Statistics, checked against hand-computable answers
# ---------------------------------------------------------------------------------------
def test_ranks_share_ties():
    ranks = V._ranks(np.array([10.0, 20.0, 20.0, 30.0]))
    assert list(ranks) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_is_one_for_a_monotone_relation():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    rho, p = V.spearman(x, np.exp(x))
    assert rho == pytest.approx(1.0)
    assert p < 0.05


def test_spearman_is_minus_one_for_a_reversed_relation():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    assert V.spearman(x, -x)[0] == pytest.approx(-1.0)


def test_spearman_survives_ties_without_inventing_a_relation():
    x = np.array([1.0, 1.0, 1.0, 1.0])
    rho, _ = V.spearman(x, np.array([1.0, 2.0, 3.0, 4.0]))
    assert math.isnan(rho)


def test_pearson_matches_a_hand_computed_value():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([2.0, 4.0, 6.0, 8.0])
    r, p = V.pearson(x, y)
    assert r == pytest.approx(1.0)
    assert p == pytest.approx(0.0, abs=1e-12)


def test_pearson_p_value_is_sane_for_a_known_r():
    # r = 0.5 with n = 20 has a two-sided p of ~0.025 (t = 2.45 on 18 df).
    n = 20
    x = np.linspace(0, 1, n)
    noise = np.array([0.10, -0.12, 0.05, -0.03, 0.09, -0.07, 0.02, -0.01, 0.04, -0.05,
                      0.03, -0.04, 0.06, -0.08, 0.01, -0.02, 0.07, -0.06, 0.0, 0.0])
    r, p = V.pearson(x, x + noise)
    assert 0.0 < r < 1.0
    assert 0.0 < p < 0.05


def test_log_linear_fit_recovers_a_known_slope():
    ndti = np.array([-0.2, -0.1, 0.0, 0.1, 0.2])
    turbidity = 10.0 ** (2.0 + 3.0 * ndti)
    fit = V.log_linear_fit(ndti, turbidity)
    assert fit["slope"] == pytest.approx(3.0)
    assert fit["intercept"] == pytest.approx(2.0)
    assert fit["r2"] == pytest.approx(1.0)
    assert fit["rmse_log10"] == pytest.approx(0.0, abs=1e-12)


def test_log_linear_fit_refuses_a_degenerate_predictor():
    fit = V.log_linear_fit(np.zeros(5), np.ones(5))
    assert fit["slope"] is None


def test_leave_one_out_is_worse_than_the_in_sample_fit():
    rng = np.random.default_rng(7)
    ndti = rng.uniform(-0.3, 0.3, 40)
    turbidity = 10.0 ** (1.0 + 2.0 * ndti + rng.normal(0.0, 0.4, 40))
    in_sample = V.log_linear_fit(ndti, turbidity)["rmse_log10"]
    loo = V.leave_one_out(ndti, turbidity)
    assert loo["rmse_log10"] > in_sample
    assert loo["factor"] == pytest.approx(10 ** loo["rmse_log10"])
    assert abs(loo["bias_log10"]) < 0.2


def test_leave_one_out_needs_four_pairs():
    assert V.leave_one_out(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0]))["factor"] \
        is None


# ---------------------------------------------------------------------------------------
# Scene deduplication
# ---------------------------------------------------------------------------------------
def _pair(site, day, water, cloud, ndti=-0.1, turbidity=10.0):
    return {"site": site, "scene": f"{site}-{day}-{water}", "scene_time": f"{day}T16:00:00Z",
            "cell_water_fraction": water, "scene_cloud_cover": cloud, "ndti": ndti,
            "ndti_water": ndti, "turbidity": turbidity, "turbidity_n": 4, "unit": "FNU"}


def test_dedupe_keeps_one_pair_per_gauge_per_day():
    pairs = [_pair("A", "2026-09-16", 0.5, 5.0), _pair("A", "2026-09-16", 1.0, 1.0),
             _pair("A", "2026-09-17", 1.0, 1.0)]
    deduped = V.dedupe_by_day(pairs)
    assert len(deduped) == 2
    assert [row["scene_time"][:10] for row in deduped] == ["2026-09-16", "2026-09-17"]


def test_dedupe_prefers_the_best_covering_granule():
    pairs = [_pair("A", "2026-09-16", 0.25, 1.0), _pair("A", "2026-09-16", 0.75, 9.0)]
    assert V.dedupe_by_day(pairs)[0]["cell_water_fraction"] == 0.75


def test_dedupe_breaks_a_coverage_tie_on_cloud():
    pairs = [_pair("A", "2026-09-16", 1.0, 30.0), _pair("A", "2026-09-16", 1.0, 2.0)]
    assert V.dedupe_by_day(pairs)[0]["scene_cloud_cover"] == 2.0


def test_dedupe_keeps_gauges_separate():
    pairs = [_pair("A", "2026-09-16", 1.0, 1.0), _pair("B", "2026-09-16", 1.0, 1.0)]
    assert len(V.dedupe_by_day(pairs)) == 2


# ---------------------------------------------------------------------------------------
# The summary, on a synthetic sample with a known answer
# ---------------------------------------------------------------------------------------
def _synthetic_pairs(n=40, noise=0.0, seed=3, water=1.0):
    rng = np.random.default_rng(seed)
    rows = []
    for index in range(n):
        ndti = -0.4 + 0.005 * index
        turbidity = 10.0 ** (1.5 + 2.0 * ndti + rng.normal(0.0, noise))
        rows.append({
            "site": f"S{index % 5}", "scene": f"sc{index}",
            "scene_time": f"2026-09-{index % 28 + 1:02d}T16:00:00Z",
            "cell_water_fraction": water, "scene_cloud_cover": 1.0,
            "ndti": ndti, "ndti_water": ndti, "turbidity": turbidity,
            "turbidity_n": 4, "unit": "FNU",
        })
    return rows


def test_summarise_reports_both_variants_and_the_strata():
    summary = V.summarise(_synthetic_pairs(noise=0.0))
    assert summary["pairs"] == 40
    assert summary["sites"] == 5
    assert summary["published_cell_value"]["spearman_rho"] == pytest.approx(1.0)
    assert summary["water_only"]["spearman_rho"] == pytest.approx(1.0)
    assert set(summary["water_fraction_strata"]) == {">=0.25", ">=0.50", ">=0.75"}
    # aliases mirror the headline variant
    assert summary["spearman_rho"] == summary["published_cell_value"]["spearman_rho"]


def test_summarise_strata_split_on_the_water_fraction():
    pairs = _synthetic_pairs(n=20, water=1.0) + _synthetic_pairs(n=20, water=0.3, seed=9)
    summary = V.summarise(pairs)
    assert summary["water_fraction_strata"][">=0.75"]["pairs"] == 20
    assert summary["water_fraction_strata"][">=0.50"]["pairs"] == 20
    assert summary["water_fraction_strata"][">=0.25"]["pairs"] == 40


def test_summarise_ignores_below_detection_turbidity():
    pairs = _synthetic_pairs(n=10)
    pairs[0]["turbidity"] = 0.0
    assert V.summarise(pairs)["pairs"] == 9


def test_summarise_says_so_when_there_is_nothing_to_report():
    summary = V.summarise([])
    assert summary["pairs"] == 0
    assert summary["published_cell_value"]["pairs"] == 0


def test_summarise_counts_a_site_centred_relation():
    summary = V.summarise(_synthetic_pairs(n=40, noise=0.05))
    block = summary["published_cell_value"]
    assert block["site_centred_spearman_rho"] is not None
    assert block["sites_within_site_rho"] == 5


# ---------------------------------------------------------------------------------------
# Gauge selection against the product grid
# ---------------------------------------------------------------------------------------
def test_cell_of_pins_the_web_mercator_arithmetic():
    """The grid is EPSG:3857: lon 0 / lat 0 is (x=0, y=0) in metres."""
    grid = {"left": -3_700_000.0, "top": 7_000_000.0, "res_m": 1848.0,
            "width": 4000, "height": 4000}
    assert V.cell_of(0.0, 0.0, grid) == (round(7_000_000.0 / 1848.0),
                                         round(3_700_000.0 / 1848.0))
    row_north, col_north = V.cell_of(0.0, 50.0, grid)
    assert row_north < V.cell_of(0.0, 0.0, grid)[0]     # north is up, rows count down
    assert col_north == V.cell_of(0.0, 0.0, grid)[1]    # same meridian, same column


def test_cell_of_matches_web_mercator_arithmetic():
    """The grid is EPSG:3857, so a known point must land on a computable cell."""
    grid = {"left": 0.0, "top": 0.0, "res_m": 1000.0, "width": 10, "height": 10}
    # lon 0, lat 0 is (0, 0) in Mercator: the top-left corner of this grid.
    assert V.cell_of(0.0, 0.0, grid) == (0, 0)


# ---------------------------------------------------------------------------------------
# Live network checks (deselected by default)
# ---------------------------------------------------------------------------------------
MAUMEE = "USGS-04193500"


@pytest.mark.network
def test_live_turbidity_fetch_returns_samples():
    samples = V.fetch_turbidity(MAUMEE, "2026-09-01", "2026-09-05")
    assert samples, "expected in-situ turbidity for the Maumee River"
    assert all(sample["value"] >= 0 for sample in samples)
    assert all(sample["unit"] == "FNU" for sample in samples)
    stamps = [sample["time"] for sample in samples]
    assert stamps == sorted(stamps)


@pytest.mark.network
def test_live_turbidity_window_is_not_truncated():
    """The collection caps a response; the chunked fetch must cover the whole window."""
    start, end = "2026-08-01", "2026-09-20"
    samples = V.fetch_turbidity(MAUMEE, start, end)
    days = {sample["time"][:10] for sample in samples}
    assert max(days) >= "2026-09-01", f"window truncated, newest day {max(days)}"


@pytest.mark.network
def test_live_scene_search_finds_a_scene_over_a_large_lake():
    scenes = V.candidate_scenes(-90.05, 35.15, "2026-09-01", "2026-09-20", max_scenes=3)
    assert scenes, "expected Sentinel-2 scenes over the Mississippi at Memphis"
    assert all(scene["properties"]["eo:cloud_cover"] <= V.MAX_GRANULE_CLOUD
               for scene in scenes)


@pytest.mark.network
def test_live_cell_ndti_on_a_turbid_cell_is_physical():
    scenes = V.candidate_scenes(-118.8434, 43.3067, "2026-08-01", "2026-09-20",
                                max_scenes=4)
    assert scenes
    values = []
    for scene in scenes:
        cell = V.cell_ndti(scene, -118.8434, 43.3067, 1848.0)
        if cell["ndti_water"] is not None:
            values.append(cell["ndti_water"])
    assert values, "expected at least one readable cell"
    for value in values:
        assert -1.0 <= value <= 1.0
