import numpy as np
import pytest

from ripple_pipeline.indices import (
    ndci,
    ndti,
    normalize_ndci,
    normalize_ndti,
    normalize_relative,
    percentile_range,
    possible_bloom_mask,
    risk_score,
)


def test_ndci_formula():
    red = np.array([[0.01]], dtype="float32")
    re1 = np.array([[0.03]], dtype="float32")
    np.testing.assert_allclose(ndci(red, re1), [[0.5]], atol=1e-6)


def test_ndti_formula():
    red = np.array([[0.09]], dtype="float32")
    green = np.array([[0.07]], dtype="float32")
    np.testing.assert_allclose(ndti(red, green), [[0.125]], atol=1e-6)


def test_indices_are_nan_safe_on_zero_denominator():
    z = np.array([[0.0, 0.0]], dtype="float32")
    assert np.isnan(ndci(z, z)[0, 0])
    assert np.isnan(ndti(z, z)[0, 0])


def test_normalize_ndci_clamps_to_unit():
    v = np.array([-0.2, 0.0, 0.15, 0.3, 0.9], dtype="float32")
    np.testing.assert_allclose(normalize_ndci(v), [0.0, 0.0, 0.5, 1.0, 1.0], atol=1e-6)


def test_normalize_ndti_uses_per_body_percentiles():
    v = np.array([0.02, 0.05, 0.10, 0.175], dtype="float32")
    got = normalize_ndti(v, p05=0.03, p95=0.15)
    np.testing.assert_allclose(got, [0.0, 1 / 6, 7 / 12, 1.0], atol=1e-5)


def test_normalize_ndti_handles_degenerate_range():
    v = np.array([0.1, 0.1], dtype="float32")
    np.testing.assert_allclose(normalize_ndti(v, p05=0.1, p95=0.1), [0.0, 0.0])


def test_risk_is_worst_of_the_two():
    ndci_v = np.array([0.1, 0.8], dtype="float32")
    ndti_v = np.array([0.9, 0.2], dtype="float32")
    np.testing.assert_allclose(risk_score(ndci_v, ndti_v), [0.9, 0.8], atol=1e-6)


def test_risk_never_exceeds_one():
    r = risk_score(np.array([1.5], dtype="float32"), np.array([1.5], dtype="float32"))
    assert r[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Regression fixtures against real Sentinel-2 scenes, measured 2026-09-14 on
# corrected reflectance (DN * 1e-4, no offset). Medians over SCL-water pixels at 250 m.
#
#   Superior  S2C_16TDT_20260901_0_L2A   clear / oligotrophic
#   ErieW     S2A_17TLG_20260828_0_L2A   turbid / eutrophic
#   Winnipeg  S2C_14UPC_20260827_0_L2A   severe summer bloom
# ---------------------------------------------------------------------------
REAL_ANCHORS = {
    "superior": dict(blue=0.0209, green=0.0123, red=0.0056, rededge1=0.0044,
                     nir=0.0041, ndci=-0.116, ndti=-0.376),
    "erie": dict(blue=0.0355, green=0.0427, red=0.0187, rededge1=0.0170,
                 nir=0.0096, ndci=-0.052, ndti=-0.382),
    "winnipeg": dict(blue=0.0377, green=0.0607, red=0.0407, rededge1=0.0500,
                     nir=0.0213, ndci=+0.047, ndti=-0.191),
}


def _scalar(fn, a, b):
    return float(fn(np.array([[a]], dtype="float32"),
                    np.array([[b]], dtype="float32"))[0, 0])


@pytest.mark.parametrize("name", list(REAL_ANCHORS))
def test_real_scene_ndti_is_reproduced(name):
    a = REAL_ANCHORS[name]
    got = _scalar(ndti, a["red"], a["green"])
    assert got == pytest.approx(a["ndti"], abs=0.02), f"{name}: {got}"


@pytest.mark.parametrize("name", list(REAL_ANCHORS))
def test_real_scene_ndci_is_reproduced(name):
    a = REAL_ANCHORS[name]
    got = _scalar(ndci, a["red"], a["rededge1"])
    assert got == pytest.approx(a["ndci"], abs=0.06), f"{name}: {got}"


def test_clear_water_decays_monotonically_from_blue_to_nir():
    """Superior is textbook clear water: blue > green > red > red-edge > NIR."""
    a = REAL_ANCHORS["superior"]
    assert a["blue"] > a["green"] > a["red"] > a["rededge1"] > a["nir"]


def test_bloom_lakes_peak_in_green():
    """Erie and Winnipeg are green-peaked -- the algal/eutrophic signature."""
    for name in ("erie", "winnipeg"):
        a = REAL_ANCHORS[name]
        assert a["green"] > a["blue"], f"{name} should peak in green"
        assert a["green"] > a["red"], f"{name} should peak in green"


def test_ndci_ranks_bloom_intensity_across_real_lakes():
    """Winnipeg (severe bloom) > Erie (patchy) > Superior (clear)."""
    ndci_of = lambda n: _scalar(  # noqa: E731
        ndci, REAL_ANCHORS[n]["red"], REAL_ANCHORS[n]["rededge1"])
    assert ndci_of("superior") < ndci_of("erie") < ndci_of("winnipeg")


def test_winnipeg_rededge_exceeds_red_by_bloom_margin():
    """Measured NDCI p90 over Winnipeg was +0.52; the median already crosses zero."""
    a = REAL_ANCHORS["winnipeg"]
    assert a["rededge1"] > a["red"]
    assert _scalar(ndci, a["red"], a["rededge1"]) > 0.0


# ---------------------------------------------------------------------------
# Relative normalization and absolute bloom flag
# ---------------------------------------------------------------------------

def test_percentile_range_handles_empty_input():
    lo, hi = percentile_range(np.array([np.nan, np.nan], dtype="float32"))
    assert lo == 0.0 and hi == 1.0


def test_percentile_range_expands_a_degenerate_span():
    """A constant field must not produce a zero-width divisor."""
    lo, hi = percentile_range(np.full(100, 0.5, dtype="float32"))
    assert hi > lo


def test_relative_normalization_spans_the_observed_range():
    v = np.linspace(-0.4, 0.6, 101, dtype="float32")
    lo, hi = percentile_range(v)
    out = normalize_relative(v, lo, hi)
    assert out.min() == pytest.approx(0.0, abs=1e-6)
    assert out.max() == pytest.approx(1.0, abs=1e-6)


def test_relative_normalization_does_not_saturate_on_a_widespread_bloom():
    """The whole point: a region-wide bloom must still render as a gradient."""
    v = np.linspace(0.30, 0.90, 50, dtype="float32")   # every pixel above the abs threshold
    lo, hi = percentile_range(v)
    out = normalize_relative(v, lo, hi)
    assert out.max() > out.min(), "gradient collapsed"
    assert out.max() == pytest.approx(1.0, abs=1e-6)


def test_absolute_bloom_flag_uses_the_published_threshold():
    v = np.array([[0.05, 0.15, np.nan, 0.30]], dtype="float32")
    flagged = possible_bloom_mask(v, threshold=0.1)
    assert flagged.tolist() == [[False, True, False, True]]


def test_bloom_flag_ignores_nan():
    v = np.array([[np.nan]], dtype="float32")
    assert not possible_bloom_mask(v, threshold=0.1).any()
