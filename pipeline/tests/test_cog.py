import numpy as np
import pytest

from ripple_pipeline.cog import read_aoi, read_aoi_reflectance, read_band_stack
from ripple_pipeline.stac import search_scenes, pick_best_scene

SUPERIOR = (-87.60, 46.70, -86.60, 47.30)


def _best_scene():
    scenes = search_scenes(SUPERIOR, "2026-08-01", "2026-09-14", limit=40)
    return pick_best_scene(scenes, SUPERIOR)[1]


@pytest.mark.network
def test_aoi_read_is_bounded_to_aoi_extent():
    arr, valid = read_aoi(_best_scene(), "green", SUPERIOR)
    assert arr.ndim == 2
    assert arr.shape == valid.shape
    # 1.0 deg lon at 47N ~= 76 km, 0.6 deg lat ~= 67 km; at 250 m that is roughly 300x270
    assert 200 <= arr.shape[0] <= 400, arr.shape
    assert 250 <= arr.shape[1] <= 400, arr.shape


@pytest.mark.network
def test_nodata_is_masked_on_raw_dn():
    raw, valid = read_aoi(_best_scene(), "green", SUPERIOR)
    assert (raw[valid] > 0).all(), "valid mask must exclude DN==0"


@pytest.mark.network
def test_reflectance_read_is_positive_over_water():
    refl, valid = read_aoi_reflectance(_best_scene(), "nir", SUPERIOR)
    assert valid.any()
    assert refl[valid].min() >= 0.0


@pytest.mark.network
def test_band_stack_shares_one_common_grid():
    refl, raw, valid = read_band_stack(_best_scene(), ("green", "red", "nir"), SUPERIOR)
    shapes = {arr.shape for arr in refl.values()}
    assert len(shapes) == 1, f"bands landed on different grids: {shapes}"
    assert set(refl) == {"green", "red", "nir"}
    assert refl["green"].shape == valid.shape


@pytest.mark.network
def test_band_stack_nan_fills_invalid_pixels():
    refl, _, valid = read_band_stack(_best_scene(), ("green", "red"), SUPERIOR)
    assert np.isnan(refl["green"][~valid]).all()


@pytest.mark.network
def test_clearest_scene_passes_scaling_validation():
    """End-to-end R1: a real clear-water scene must validate."""
    from ripple_pipeline.scaling import validate_scaling

    refl, _, valid = read_band_stack(
        _best_scene(), ("blue", "green", "red", "rededge1", "nir"), SUPERIOR)
    validate_scaling(refl, valid, "superior")
    # and confirm the spectrum is blue-dominant as expected over clear water
    assert refl["blue"][valid].mean() > refl["red"][valid].mean()
