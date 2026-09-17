import numpy as np
import pytest

from ripple_pipeline.scaling import ScalingError, to_reflectance, validate_scaling


def _spectrum(**overrides):
    base = {"blue": 0.0201, "green": 0.0130, "red": 0.0077,
            "rededge1": 0.0057, "nir": 0.0059}
    base.update(overrides)
    return {k: np.full((4, 4), v, dtype="float32") for k, v in base.items()}


ALL = np.ones((4, 4), dtype=bool)


def test_reflectance_uses_no_offset():
    dn = np.array([[0, 59, 130, 201, 4521]], dtype="uint16")
    got = to_reflectance(dn)
    assert got.dtype == np.float32
    np.testing.assert_allclose(got, [[0.0, 0.0059, 0.0130, 0.0201, 0.4521]], atol=1e-6)


def test_no_negative_reflectance_applied():
    """The STAC offset of -0.1 must NOT be applied: clear-water NIR would go negative."""
    dn = np.array([[59]], dtype="uint16")
    assert to_reflectance(dn)[0, 0] > 0


def test_rejects_float_input():
    with pytest.raises(TypeError, match="raw integer DN"):
        to_reflectance(np.array([[0.02]], dtype="float32"))


def test_clear_water_spectrum_validates():
    validate_scaling(_spectrum(), ALL, "superior")


def test_turbid_lake_with_high_nir_still_validates():
    """Lake Winnipeg legitimately shows elevated NIR; that is not a scaling failure."""
    validate_scaling(
        _spectrum(blue=0.035, green=0.043, red=0.019, rededge1=0.021, nir=0.030),
        ALL, "winnipeg")


def test_offset_signature_is_rejected():
    """The bug drives every band to roughly -0.09."""
    broken = {k: np.full((4, 4), v, dtype="float32") for k, v in {
        "blue": -0.0798, "green": -0.0872, "red": -0.0929,
        "rededge1": -0.0943, "nir": -0.0947}.items()}
    with pytest.raises(ScalingError, match="negative"):
        validate_scaling(broken, ALL, "unit")


def test_small_negative_floor_is_tolerated():
    """A source that applies the baseline-04 offset correctly may show tiny negatives."""
    validate_scaling(_spectrum(nir=-0.0005), ALL, "unit")


def test_inverted_spectra_are_reported_but_do_not_halt_a_mosaic():
    """Great Salt Lake is legitimately red-dominant: hypersaline, sediment-loaded water.

    The ordering test is a heuristic about clear water, so it is reported for the region
    mosaic rather than enforced. The bug it was written to catch -- a double-applied
    offset -- is caught by the negativity floor, which stays hard.
    """
    stats = validate_scaling(_spectrum(blue=0.02, red=0.05), ALL, "unit")
    assert stats["ordering_checked"] is True
    assert stats["blue_dominant"] is False
    assert stats["blue_median_over_surface"] == pytest.approx(0.02, abs=1e-4)
    assert stats["red_median_over_surface"] == pytest.approx(0.05, abs=1e-4)


def test_ordering_can_still_be_enforced_when_a_caller_wants_it():
    with pytest.raises(ScalingError, match="inverted"):
        validate_scaling(_spectrum(blue=0.02, red=0.05), ALL, "unit",
                         enforce_ordering=True)

    water = np.zeros((4, 4), dtype=bool)
    water[:, 0] = True
    refl = _spectrum()
    refl["blue"] = np.full((4, 4), 0.02, dtype="float32")
    refl["red"] = np.full((4, 4), 0.09, dtype="float32")
    with pytest.raises(ScalingError, match="inverted"):
        validate_scaling(refl, ALL, "unit", water=water, enforce_ordering=True)


def test_ordering_reports_blue_dominance_when_present():
    stats = validate_scaling(_spectrum(), ALL, "clear")
    assert stats["blue_dominant"] is True


def test_land_dominated_mosaic_is_not_halted_by_water_ordering():
    """A region mosaic is mostly vegetation, where blue < red is normal.

    Blue 0.02 / red 0.05 is a realistic land spectrum, but the water pixels here are
    correctly blue-dominant, so the reported statistic should say so.
    """
    water = np.zeros((4, 4), dtype=bool)
    water[0, :2] = True
    refl = {
        "blue": np.full((4, 4), 0.02, dtype="float32"),
        "green": np.full((4, 4), 0.03, dtype="float32"),
        "red": np.full((4, 4), 0.05, dtype="float32"),
        "rededge1": np.full((4, 4), 0.06, dtype="float32"),
        "nir": np.full((4, 4), 0.30, dtype="float32"),
    }
    # the two water pixels are blue-dominant, so the statistic is positive
    refl["blue"][0, :2] = 0.10
    refl["red"][0, :2] = 0.02
    stats = validate_scaling(refl, ALL, "region", water=water)
    assert stats["blue_dominant"] is True
    assert stats["blue_median_over_surface"] == pytest.approx(0.10, abs=1e-4)


def test_ordering_is_skipped_when_mosaic_has_no_water():
    """No water pixels means nothing to order-check; must not raise."""
    refl = {
        "blue": np.full((4, 4), 0.02, dtype="float32"),
        "green": np.full((4, 4), 0.03, dtype="float32"),
        "red": np.full((4, 4), 0.05, dtype="float32"),
        "rededge1": np.full((4, 4), 0.06, dtype="float32"),
        "nir": np.full((4, 4), 0.30, dtype="float32"),
    }
    stats = validate_scaling(refl, ALL, "all-land", water=np.zeros((4, 4), dtype=bool))
    assert stats["ordering_checked"] is False


def test_ordering_statistic_uses_water_pixels_when_supplied():
    water = np.zeros((4, 4), dtype=bool)
    water[:, 0] = True
    refl = _spectrum()
    refl["blue"] = np.full((4, 4), 0.02, dtype="float32")
    refl["red"] = np.full((4, 4), 0.09, dtype="float32")
    # red-dominant everywhere, but the statistic is reported over the water column only
    stats = validate_scaling(refl, ALL, "unit", water=water)
    assert stats["ordering_checked"] is True
    assert stats["blue_dominant"] is False


def test_rejects_degenerate_blank_read():
    with pytest.raises(ScalingError, match="degenerate"):
        validate_scaling(
            {k: np.full((4, 4), 0.001, dtype="float32")
             for k in ("blue", "green", "red", "rededge1", "nir")},
            ALL, "unit")


def test_rejects_nan_in_valid_region():
    refl = _spectrum()
    refl["green"][0, 0] = np.nan
    with pytest.raises(ScalingError, match="non-finite"):
        validate_scaling(refl, ALL, "unit")


def test_rejects_empty_valid_mask():
    with pytest.raises(ScalingError, match="no valid pixels"):
        validate_scaling(_spectrum(), np.zeros((4, 4), dtype=bool), "unit")
