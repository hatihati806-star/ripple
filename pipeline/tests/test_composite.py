import numpy as np

from ripple_pipeline.composite import Observation, best_pixel


def _obs(values, valid, age):
    return Observation(values=np.array(values, dtype="float32"),
                       valid=np.array(valid, dtype=bool), age_days=age)


def test_prefers_newest_valid_pixel():
    older = _obs([[0.9, np.nan]], [[True, False]], 10.0)
    newer = _obs([[0.2, 0.4]], [[True, True]], 2.0)
    value, age, src = best_pixel([older, newer])
    np.testing.assert_allclose(value, [[0.2, 0.4]], atol=1e-6)
    np.testing.assert_allclose(age, [[2.0, 2.0]], atol=1e-6)
    assert src.tolist() == [[1, 1]]


def test_falls_back_to_older_pixel_where_newer_is_invalid():
    older = _obs([[0.9, 0.8]], [[True, True]], 10.0)
    newer = _obs([[0.2, np.nan]], [[True, False]], 2.0)
    value, age, src = best_pixel([older, newer])
    np.testing.assert_allclose(value, [[0.2, 0.8]], atol=1e-6)
    np.testing.assert_allclose(age, [[2.0, 10.0]], atol=1e-6)
    assert src.tolist() == [[1, 0]]


def test_order_independent():
    a = _obs([[0.5]], [[True]], 9.0)
    b = _obs([[0.1]], [[True]], 1.0)
    assert best_pixel([a, b])[0][0, 0] == np.float32(0.1)
    assert best_pixel([b, a])[0][0, 0] == np.float32(0.1)


def test_uncovered_pixels_report_nan_and_minus_one_source():
    empty = _obs([[np.nan]], [[False]], 5.0)
    value, age, src = best_pixel([empty])
    assert np.isnan(value[0, 0])
    assert age[0, 0] == 0.0
    assert src[0, 0] == -1


def test_empty_input_returns_empty_arrays():
    value, age, src = best_pixel([])
    assert value.size == 0 and age.size == 0 and src.size == 0


def test_shape_mismatch_is_rejected():
    import pytest

    a = _obs([[0.1, 0.2]], [[True, True]], 1.0)
    b = _obs([[0.1]], [[True]], 1.0)
    with pytest.raises(ValueError, match="shape"):
        best_pixel([a, b])
