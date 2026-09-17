import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ripple_pipeline.cli import (
    _grid_window,
    build_grid,
    ocean_search_cells,
    plan_windows,
    probe_stride,
    risk_from,
    scenes_in_window,
    write_manifest,
)

MINNESOTA = (-97.5, 43.5, -89.5, 47.5)


def test_grid_uses_requested_resolution_when_small():
    grid = build_grid(MINNESOTA, res_m=250.0, max_dim=4096)
    assert grid["res_m"] == pytest.approx(250.0)
    assert grid["crs"] == "EPSG:3857"
    # 8 deg of longitude is ~630 km on the ground at 45N, but Mercator stretches
    # longitude by 1/cos(lat), giving ~890 km and ~3560 px at 250 m.
    assert 3400 <= grid["width"] <= 3700, grid["width"]
    # 4 deg of latitude is ~445 km on the ground, ~637 km in Mercator -> ~2550 px
    assert 2300 <= grid["height"] <= 2800, grid["height"]


def test_grid_is_capped_for_large_regions():
    """The full v1 region at 250 m is ~7500 px wide, past comfortable texture limits."""
    full = (-106.0, 40.0, -82.0, 54.0)
    grid = build_grid(full, res_m=250.0, max_dim=4096)
    assert grid["width"] <= 4096, grid["width"]
    assert grid["height"] <= 4096, grid["height"]
    assert grid["res_m"] > grid["requested_res_m"]
    assert grid["requested_res_m"] == 250.0


def test_grid_width_height_are_positive():
    grid = build_grid((-90.0, 45.0, -89.0, 46.0))
    assert grid["width"] > 0 and grid["height"] > 0


def test_grid_window_covers_full_grid_for_identical_bounds():
    grid = build_grid(MINNESOTA)
    window = _grid_window(grid, grid["merc_bounds"])
    assert window is not None
    assert window.col_off == 0 and window.row_off == 0
    assert window.width >= grid["width"] - 1
    assert window.height >= grid["height"] - 1


def test_grid_window_returns_none_for_non_overlapping_bounds():
    grid = build_grid(MINNESOTA)
    assert _grid_window(grid, (-1e7, -1e7, -9e6, -9e6)) is None


def test_grid_window_is_clipped_to_grid():
    grid = build_grid(MINNESOTA)
    huge = (-2e7, -2e7, 2e7, 2e7)
    window = _grid_window(grid, huge)
    assert window.col_off >= 0 and window.row_off >= 0
    assert window.col_off + window.width <= grid["width"] + 1
    assert window.row_off + window.height <= grid["height"] + 1


def test_manifest_round_trips(tmp_path):
    manifest = {
        "generated_utc": "2026-09-14T00:00:00Z",
        "region_bbox": list(MINNESOTA),
        "resolution_m": 250.0,
        "palette": [],
        "scales": {"ndci": [0.0, 0.1, 0.3], "ndti": [0.05, 0.15]},
        "frames": [{"id": "obs-latest", "kind": "observed",
                    "label": "Latest observed composite",
                    "tile": "tiles/obs-latest.png", "age_days": 3.0}],
        "disclaimer": "not a regulatory measurement",
    }
    path = write_manifest(tmp_path, manifest)
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["frames"][0]["kind"] == "observed"
    assert loaded["scales"]["ndti"] == [0.05, 0.15]


def test_forecast_risk_is_bounded_and_zero_rain_gives_baseline():
    from ripple_pipeline.cli import forecast_runoff_risk

    pytest.importorskip("urllib.request")
    try:
        series = forecast_runoff_risk(44.98, -93.27, "row_crop", "B", baseline_risk=0.2)
    except Exception as exc:  # network unavailable
        pytest.skip(f"network required: {exc}")

    assert len(series) == 7
    for point in series:
        assert 0.0 <= point["risk"] <= 1.0
        assert point["rain_mm"] >= 0.0
        assert point["runoff_mm"] >= 0.0
        assert point["curve_number"] >= 30.0
        if point["rain_mm"] == 0.0:
            assert point["runoff_mm"] == 0.0
            assert point["risk"] == pytest.approx(0.2)


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_plan_windows_orders_oldest_first_and_ends_at_now():
    windows = plan_windows(NOW, frames=4, step_days=5, window_days=6)
    assert len(windows) == 4
    assert windows[-1][1] == NOW
    for (start, end) in windows:
        assert (end - start) == timedelta(days=6)
    assert windows[0][1] == NOW - timedelta(days=15)
    starts = [start for start, _ in windows]
    assert starts == sorted(starts)


def test_plan_windows_handles_a_single_frame():
    windows = plan_windows(NOW, frames=1, step_days=5, window_days=6)
    assert len(windows) == 1
    assert windows[0][1] == NOW
    assert windows[0][0] == NOW - timedelta(days=6)


def test_scenes_in_window_filters_by_acquisition_date_inclusive():
    scenes = [
        {"id": "a", "properties": {"datetime": "2026-09-15T17:30:00.000Z"}},
        {"id": "b", "properties": {"datetime": "2026-09-10T10:00:00.000Z"}},
        {"id": "c", "properties": {"datetime": "2026-09-01T00:00:00.000Z"}},
        {"id": "d", "properties": {}},
    ]
    picked = scenes_in_window(scenes, datetime(2026, 9, 9, tzinfo=timezone.utc),
                              datetime(2026, 9, 16, tzinfo=timezone.utc))
    # Both edges are included: the window label reads "Sep 9 - Sep 16".
    assert [scene["id"] for scene in picked] == ["a", "b"]
    same_day = scenes_in_window(scenes, datetime(2026, 9, 15, tzinfo=timezone.utc),
                                datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert [scene["id"] for scene in same_day] == ["a"]


def test_probe_stride_keeps_the_long_edge_within_the_cap():
    grid = {"width": 4096, "height": 3533}
    assert probe_stride(grid, 1024) == 4
    assert probe_stride(grid, 512) == 8
    assert probe_stride(grid, 8192) == 1


def test_risk_from_clamps_to_the_shared_normalization_and_masks_invalid():
    ndci = np.array([[0.0, 0.1], [5.0, -5.0]], dtype="float32")
    ndti = np.array([[0.2, 0.2], [0.2, 0.2]], dtype="float32")
    valid = np.array([[True, True], [True, False]])
    frame = {"ndci": ndci, "ndti": ndti, "valid": valid}

    risk = risk_from(frame, ndci_bounds=(0.0, 0.1), ndti_bounds=(0.2, 0.2))
    assert risk[0, 0] == pytest.approx(0.0)
    assert risk[0, 1] == pytest.approx(1.0)
    assert risk[1, 0] == pytest.approx(1.0)
    assert np.isnan(risk[1, 1])


def test_risk_from_is_monotonic_in_the_index():
    frame = {
        "ndci": np.array([[0.02, 0.04, 0.06]], dtype="float32"),
        "ndti": np.full((1, 3), np.nan, dtype="float32"),
        "valid": np.ones((1, 3), dtype=bool),
    }
    risk = risk_from(frame, ndci_bounds=(0.0, 0.1), ndti_bounds=(0.0, 1.0))
    assert risk[0, 0] < risk[0, 1] < risk[0, 2]


def test_ocean_search_cells_skips_only_fully_ocean_cells():
    grid = build_grid((-10.0, 40.0, -7.0, 42.0), max_dim=2048)
    ocean = np.zeros((grid["height"], grid["width"]), dtype=bool)
    ocean[:, : int(grid["width"] * 0.60)] = True

    skip = ocean_search_cells(ocean, grid, cell_deg=1.0)
    assert (-10.0, 40.0) in skip
    assert (-8.0, 40.0) not in skip


def test_ocean_search_cells_is_empty_for_a_land_region():
    grid = build_grid(MINNESOTA, max_dim=1024)
    ocean = np.zeros((grid["height"], grid["width"]), dtype=bool)
    assert ocean_search_cells(ocean, grid, cell_deg=1.5) == frozenset()
