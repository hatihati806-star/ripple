import numpy as np
import pytest

from ripple_pipeline.runoff import (
    AMC_DRY_RAIN_MM,
    AMC_WET_RAIN_MM,
    EROSION_FACTORS,
    cn_from_antecedent_rainfall,
    curve_number,
    curve_number_from_rainfall,
    direct_runoff_mm,
    load_risk,
    runoff_from_cn,
)


def test_zero_rainfall_gives_zero_runoff():
    assert direct_runoff_mm(0.0, cn=80.0) == 0.0


def test_runoff_below_initial_abstraction_is_zero():
    # CN 80 -> S = 63.5 mm, Ia = 12.7 mm
    assert direct_runoff_mm(10.0, cn=80.0) == 0.0
    assert direct_runoff_mm(12.7, cn=80.0) == 0.0


def test_runoff_increases_monotonically_with_rainfall():
    values = [direct_runoff_mm(p, cn=80.0) for p in (15, 25, 50, 100)]
    assert values == sorted(values)
    assert values[-1] > values[0]


def test_runoff_increases_with_curve_number():
    assert direct_runoff_mm(50.0, cn=90.0) > direct_runoff_mm(50.0, cn=60.0)


def test_runoff_never_exceeds_rainfall():
    for rain in (5, 20, 60, 200):
        assert direct_runoff_mm(rain, cn=95.0) <= rain


def test_curve_number_rises_with_antecedent_moisture():
    dry = curve_number("row_crop", "B", antecedent_moisture=0.10)
    wet = curve_number("row_crop", "B", antecedent_moisture=0.45)
    assert wet > dry


def test_curve_number_falls_as_land_cover_gets_less_erodible():
    assert curve_number("bare", "B", 0.2) > curve_number("forest", "B", 0.2)


def test_curve_number_clamped_to_valid_range():
    for cover in EROSION_FACTORS:
        for soil in ("A", "B", "C", "D"):
            cn = curve_number(cover, soil, 0.25)
            assert 30.0 <= cn <= 100.0


def test_load_risk_scales_with_erosion_factor():
    assert load_risk(10.0, "bare") > load_risk(10.0, "forest")
    assert load_risk(10.0, "water") == 0.0


def test_load_risk_is_zero_when_no_runoff():
    assert load_risk(0.0, "row_crop") == 0.0


def test_unknown_land_cover_rejected():
    with pytest.raises(KeyError):
        curve_number("lava", "B", 0.2)


def test_unknown_soil_group_rejected():
    with pytest.raises(KeyError, match="soil group"):
        curve_number("forest", "Z", 0.2)


@pytest.mark.network
def test_forecast_client_returns_hourly_precipitation():
    from ripple_pipeline.weather import fetch_forecast, daily_rainfall, mean_antecedent_moisture

    point = fetch_forecast(44.98, -93.27, days=7, past_days=2)
    assert "hourly" in point
    assert len(point["hourly"]["time"]) == 9 * 24  # 2 past + 7 forecast days
    days = daily_rainfall(point)
    assert len(days) == 9
    assert all(isinstance(mm, float) and mm >= 0.0 for _, mm in days)
    assert 0.0 <= mean_antecedent_moisture(point) <= 1.0


@pytest.mark.network
def test_runoff_responds_to_real_forecast_rainfall():
    """Integration: a real forecast must produce a non-negative, bounded risk series."""
    from ripple_pipeline.weather import daily_rainfall, fetch_forecast, mean_antecedent_moisture

    point = fetch_forecast(44.98, -93.27, days=7)
    moisture = mean_antecedent_moisture(point)
    cn = curve_number("row_crop", "B", moisture)
    for _, rain_mm in daily_rainfall(point):
        runoff = direct_runoff_mm(rain_mm, cn)
        risk = load_risk(runoff, "row_crop")
        assert runoff >= 0.0 and runoff <= max(rain_mm, 1e-9)
        assert risk >= 0.0


def test_antecedent_rainfall_amc_breakpoints():
    assert cn_from_antecedent_rainfall("row_crop", "B", 0.0) == pytest.approx(72.0)
    assert cn_from_antecedent_rainfall("row_crop", "B", AMC_DRY_RAIN_MM) == pytest.approx(72.0)
    assert cn_from_antecedent_rainfall("row_crop", "B", 20.0) == pytest.approx(78.0)
    assert cn_from_antecedent_rainfall("row_crop", "B", AMC_WET_RAIN_MM) == pytest.approx(86.0)
    assert cn_from_antecedent_rainfall("row_crop", "B", 90.0) == pytest.approx(86.0)


def test_antecedent_rainfall_cn_is_clamped():
    for cover in EROSION_FACTORS:
        for soil in ("A", "B", "C", "D"):
            value = cn_from_antecedent_rainfall(cover, soil, 60.0)
            assert 30.0 <= float(value) <= 100.0


def test_antecedent_rainfall_cn_accepts_arrays():
    rain = np.array([0.0, 20.0, 60.0])
    out = cn_from_antecedent_rainfall("pasture", "C", rain)
    assert out.shape == rain.shape
    assert out[0] < out[1] < out[2]


def test_scalar_and_array_cn_agree():
    for rain in (0.0, 12.9, 13.1, 27.9, 28.1, 100.0):
        scalar = curve_number_from_rainfall("forest", "D", rain)
        array = float(cn_from_antecedent_rainfall("forest", "D", np.float64(rain)))
        assert scalar == pytest.approx(array)


def test_vectorized_runoff_matches_the_scalar_model():
    for rain in (0.0, 4.0, 12.7, 25.0, 60.0, 150.0):
        for cn in (30.0, 55.0, 78.0, 95.0, 100.0):
            expected = direct_runoff_mm(rain, cn)
            got = float(runoff_from_cn(np.float64(rain), np.float64(cn)))
            assert got == pytest.approx(expected, abs=1e-9)


def test_vectorized_runoff_handles_arrays_and_nan():
    rain = np.array([0.0, 20.0, np.nan])
    out = runoff_from_cn(rain, 80.0)
    assert out.shape == rain.shape
    assert out[0] == 0.0
    assert out[1] > 0.0
    assert np.isnan(out[2])
