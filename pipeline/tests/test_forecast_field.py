import numpy as np
import pytest

from ripple_pipeline.forecast_field import (
    anomaly_fields,
    box_blur,
    fetch_rainfall_field,
    interp_field,
    loading_anomaly,
    upsample_repeat,
)


def test_box_blur_preserves_a_constant_field():
    values = np.full((8, 12), 3.5, dtype="float32")
    np.testing.assert_allclose(box_blur(values, k=5), values, rtol=1e-6)


def test_box_blur_smooths_a_spike_and_preserves_the_mean():
    values = np.zeros((9, 9), dtype="float32")
    values[4, 4] = 100.0
    smoothed = box_blur(values, k=3)
    assert smoothed[4, 4] < 100.0
    assert smoothed[4, 3] > 0.0
    assert smoothed.sum() == pytest.approx(values.sum(), rel=1e-6)


def test_box_blur_is_a_noop_for_small_k():
    values = np.arange(16, dtype="float32").reshape(4, 4)
    np.testing.assert_array_equal(box_blur(values, k=1), values)


def test_interp_field_hits_the_corners_exactly():
    coarse = np.array([[0.0, 2.0], [4.0, 6.0]])
    fine = interp_field(coarse, out_h=5, out_w=5)
    assert fine[0, 0] == pytest.approx(0.0)
    assert fine[0, -1] == pytest.approx(2.0)
    assert fine[-1, 0] == pytest.approx(4.0)
    assert fine[-1, -1] == pytest.approx(6.0)
    assert fine[2, 2] == pytest.approx(3.0)


def test_interp_field_is_linear_along_an_axis():
    coarse = np.array([[0.0], [10.0]])
    fine = interp_field(coarse, out_h=11, out_w=1)[:, 0]
    np.testing.assert_allclose(fine, np.linspace(0.0, 10.0, 11), atol=1e-9)


def test_upsample_repeat_crops_to_the_exact_shape():
    coarse = np.array([[1.0, 2.0], [3.0, 4.0]])
    big = upsample_repeat(coarse, out_h=5, out_w=7)
    assert big.shape == (5, 7)
    assert big[0, 0] == 1.0
    assert big[0, -1] == 2.0
    assert big[-1, 0] == 3.0
    assert big[-1, -1] == 4.0


def test_zero_rainfall_produces_no_anomaly():
    assert loading_anomaly(0.0, antecedent_mm=30.0) == 0.0


def test_anomaly_increases_with_rainfall():
    dry = loading_anomaly(5.0, 5.0)
    wet = loading_anomaly(40.0, 5.0)
    assert wet > dry


def test_anomaly_increases_with_antecedent_moisture():
    dry = loading_anomaly(25.0, 2.0)
    wet = loading_anomaly(25.0, 40.0)
    assert wet > dry


def test_anomaly_is_bounded():
    for rain in (0.0, 10.0, 100.0, 500.0):
        value = loading_anomaly(rain, 50.0)
        assert 0.0 <= value <= 1.0


def test_anomaly_accepts_arrays():
    rain = np.array([[0.0, 20.0], [40.0, 5.0]])
    antecedent = np.full_like(rain, 10.0)
    out = loading_anomaly(rain, antecedent)
    assert out.shape == rain.shape
    assert out[0, 0] == 0.0
    assert out[1, 0] > out[0, 1]


def test_anomaly_fields_match_the_grid_shape():
    n_lat, n_lon = 4, 6
    rainfall = {
        "dates": ["2026-09-16", "2026-09-17"],
        "antecedent": np.full((n_lat, n_lon), 15.0),
        # 40 mm clears the SCS initial abstraction at CN 78; 12 mm would not.
        "rain": [np.full((n_lat, n_lon), 40.0), np.zeros((n_lat, n_lon))],
    }
    fields = anomaly_fields(rainfall, out_h=64, out_w=96, smooth_k=3)
    assert len(fields) == 2
    for field in fields:
        assert field.shape == (64, 96)
        assert field.dtype == np.float32
    assert fields[0].mean() > 0.0
    assert fields[1].mean() == pytest.approx(0.0, abs=1e-6)


@pytest.mark.network
def test_rainfall_field_matches_the_requested_shape():
    data = fetch_rainfall_field(-97.0, 49.0, -95.0, 51.0, n_lon=4, n_lat=3, days=3)
    assert data["antecedent"].shape == (3, 4)
    assert len(data["rain"]) == 3
    assert all(day.shape == (3, 4) for day in data["rain"])
    assert all(mm >= 0.0 for day in data["rain"] for mm in day.ravel())
    assert len(data["dates"]) == 3
