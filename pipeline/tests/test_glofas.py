import pytest

from ripple_pipeline.glofas import DischargeForecast, fetch_discharge, has_meaningful_river

MEMPHIS = (35.15, -90.05)
SASKATCHEWAN_DRY = (50.5, -105.5)
KANSAS_FARMLAND = (39.0, -95.5)


def _fc(median):
    return DischargeForecast(dates=["d"] * len(median), p25=[], median=median,
                             p75=[], mean=[])


def test_empty_series_is_not_a_river():
    assert not has_meaningful_river(_fc([]))


def test_snapped_dry_cell_values_are_rejected():
    assert not has_meaningful_river(_fc([0.02, 0.05, 0.08]))
    assert not has_meaningful_river(_fc([0.04, 0.01, 0.01]))


def test_floor_is_inclusive():
    assert has_meaningful_river(_fc([1.0]))
    assert not has_meaningful_river(_fc([0.99]))


def test_real_river_discharge_is_accepted():
    assert has_meaningful_river(_fc([16.82, 16.10, 15.26]))


@pytest.mark.network
def test_snapped_dry_cell_is_rejected_live():
    """The API returns a number here; the guard must reject it."""
    fc = fetch_discharge(*SASKATCHEWAN_DRY, forecast_days=3, past_days=0)
    assert not has_meaningful_river(fc), f"expected rejection, got {fc.median}"


@pytest.mark.network
def test_kansas_farmland_is_rejected_live():
    fc = fetch_discharge(*KANSAS_FARMLAND, forecast_days=3, past_days=0)
    assert not has_meaningful_river(fc), f"expected rejection, got {fc.median}"


@pytest.mark.network
def test_real_river_is_accepted_live():
    fc = fetch_discharge(*MEMPHIS, forecast_days=3, past_days=0)
    assert has_meaningful_river(fc)
    assert fc.median[0] > 1.0


@pytest.mark.network
def test_ensemble_band_is_ordered():
    fc = fetch_discharge(*MEMPHIS, forecast_days=5, past_days=0)
    assert len(fc.dates) == len(fc.median)
    for lo, mid, hi in zip(fc.p25, fc.median, fc.p75):
        assert lo <= mid <= hi
