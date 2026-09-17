import numpy as np
import pytest
from affine import Affine

from ripple_pipeline.config import REGION_BBOX
from ripple_pipeline.waters import (
    CATALOG,
    Body,
    body_rc,
    nearest_valid,
    sample_body,
    search_radius_cells,
)

BBOX = REGION_BBOX


def fake_grid(width=200, height=100):
    """A synthetic Mercator grid spanning roughly the real region."""
    left, bottom, right, top = -1.18e7, 4.86e6, -9.13e6, 7.19e6
    res = (right - left) / width
    return {
        "width": width,
        "height": height,
        "transform": Affine(res, 0.0, left, 0.0, -res, top),
        "res_m": res,
        "merc_bounds": (left, bottom, right, top),
    }


def test_catalog_is_well_formed():
    ids = [body.id for body in CATALOG]
    assert len(ids) == len(set(ids)), "duplicate water body ids"
    assert len(ids) >= 20
    for body in CATALOG:
        assert body.id == body.id.lower().replace(" ", "-")
        lon_min, lat_min, lon_max, lat_max = BBOX
        assert lon_min <= body.lon <= lon_max, body.id
        assert lat_min <= body.lat <= lat_max, body.id
        assert body.kind in {"lake", "reservoir", "bay"}


def test_body_rc_maps_known_point_inside_grid():
    grid = fake_grid()
    row, col = body_rc(grid, CATALOG[0]) or (None, None)
    assert row is not None
    assert 0 <= row < grid["height"]
    assert 0 <= col < grid["width"]


def test_body_rc_returns_none_outside_grid():
    grid = fake_grid()
    outside = Body("x", "X", "lake", 10.0, 10.0)
    assert body_rc(grid, outside) is None


def test_search_radius_scales_with_resolution():
    assert search_radius_cells(652.0) == 19
    assert search_radius_cells(7000.0) == 3
    assert search_radius_cells(100.0) == 120


def test_nearest_valid_prefers_the_closest_true_cell():
    valid = np.zeros((9, 9), dtype=bool)
    valid[4, 6] = True
    valid[2, 2] = True
    assert nearest_valid(valid, 4, 4, radius_cells=3) == (4, 6)


def test_nearest_valid_respects_the_search_radius():
    valid = np.zeros((9, 9), dtype=bool)
    valid[0, 0] = True
    assert nearest_valid(valid, 4, 4, radius_cells=3) is None


def test_nearest_valid_clips_at_the_grid_edge():
    valid = np.zeros((5, 5), dtype=bool)
    valid[0, 0] = True
    assert nearest_valid(valid, 0, 0, radius_cells=10) == (0, 0)


def test_sample_body_reports_values_and_offset():
    risk = np.full((5, 5), np.nan, dtype="float32")
    ndci = np.full((5, 5), np.nan, dtype="float32")
    ndti = np.full((5, 5), np.nan, dtype="float32")
    valid = np.zeros((5, 5), dtype=bool)
    risk[2, 3] = 0.42
    ndci[2, 3] = 0.11
    ndti[2, 3] = -0.2
    valid[2, 3] = True

    values = sample_body(risk, ndci, ndti, valid, CATALOG[0], 2, 2, radius_cells=2)
    assert values["valid"] is True
    assert values["risk"] == pytest.approx(0.42)
    assert values["ndci"] == pytest.approx(0.11)
    assert values["ndti"] == pytest.approx(-0.2)
    assert values["offset_cells"] == 1


def test_sample_body_reports_invalid_when_no_water_nearby():
    shape = (5, 5)
    empty = np.full(shape, np.nan, dtype="float32")
    values = sample_body(empty, empty, empty, np.zeros(shape, dtype=bool),
                         CATALOG[0], 2, 2)
    assert values["valid"] is False
    assert values["risk"] is None
    assert values["offset_cells"] is None
