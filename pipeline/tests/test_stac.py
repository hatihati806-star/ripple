import pytest

from ripple_pipeline import stac
from ripple_pipeline.stac import (
    aoi_coverage,
    collect_scenes_by_window,
    pick_best_scene,
    rank_by_aoi_coverage,
    search_scenes,
)

ERIE = (-83.25, 41.65, -82.75, 41.95)
SUPERIOR = (-87.60, 46.70, -86.60, 47.30)


def test_collect_by_window_searches_each_window_separately(monkeypatch):
    from datetime import datetime, timezone

    calls = []

    def fake_search(bbox, start, end, limit=40, collection="sentinel-2-l2a"):
        calls.append((start, end, limit))
        key = f"{round(bbox[0], 2)}-{round(bbox[1], 2)}"
        return [
            {
                "id": f"s-{start}-{key}",
                "properties": {"datetime": f"{start}T10:00:00Z", "eo:cloud_cover": 5.0},
            },
            {
                "id": f"cloudy-{start}-{key}",
                "properties": {"datetime": f"{start}T10:00:00Z", "eo:cloud_cover": 95.0},
            },
        ]

    monkeypatch.setattr(stac, "search_scenes", fake_search)
    windows = [
        (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 5, tzinfo=timezone.utc)),
        (datetime(2026, 9, 6, tzinfo=timezone.utc), datetime(2026, 9, 10, tzinfo=timezone.utc)),
    ]
    per_window = collect_scenes_by_window((0.0, 0.0, 3.0, 2.0), windows, cell_deg=1.5,
                                          per_cell=7, workers=2)

    assert len(per_window) == 2
    # 2x2 search cells per window, each contributing one clear scene.
    assert [len(window) for window in per_window] == [4, 4]
    # Cloudy candidates are dropped rather than consuming a read later.
    assert not any(scene["id"].startswith("cloudy") for window in per_window for scene in window)
    # Each window was queried with its own date bounds, end inclusive to the day.
    bounds = {(start, end) for start, end, _ in calls}
    assert ("2026-09-01", "2026-09-06") in bounds
    assert ("2026-09-06", "2026-09-11") in bounds
    assert all(limit == 7 for _, _, limit in calls)


def test_collect_by_window_survives_search_failures(monkeypatch):
    from datetime import datetime, timezone

    def exploding_search(bbox, start, end, limit=40, collection="sentinel-2-l2a"):
        raise RuntimeError("STAC is down")

    monkeypatch.setattr(stac, "search_scenes", exploding_search)
    windows = [
        (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 5, tzinfo=timezone.utc)),
    ]
    per_window = collect_scenes_by_window((0.0, 0.0, 1.5, 1.5), windows, workers=2)
    assert per_window == [[]]


@pytest.mark.network
def test_search_returns_scenes_with_word_named_assets():
    scenes = search_scenes(ERIE, "2026-07-01", "2026-09-14", limit=5)
    assert scenes, "expected at least one scene"
    assets = scenes[0]["assets"]
    for key in ("blue", "green", "red", "rededge1", "nir", "scl"):
        assert key in assets, f"missing asset {key}"
    assert "B05" not in assets, "asset keys are word-named, not band-coded"


@pytest.mark.network
def test_ranking_finds_a_near_complete_scene():
    scenes = search_scenes(ERIE, "2026-07-01", "2026-09-14", limit=40)
    frac, best = pick_best_scene(scenes, ERIE)
    assert frac > 0.95, f"expected a near-complete scene, got {frac:.2f}"


@pytest.mark.network
def test_ranking_beats_naive_lowest_cloud_pick():
    """R2: the least-cloudy scene does NOT necessarily cover the AOI."""
    scenes = search_scenes(ERIE, "2026-07-01", "2026-09-14", limit=40)
    by_cloud = min(scenes, key=lambda s: s["properties"]["eo:cloud_cover"])
    cloud_cover_frac = aoi_coverage(by_cloud, ERIE)
    picked_frac, _ = pick_best_scene(scenes, ERIE)
    assert picked_frac >= cloud_cover_frac
    # documented behaviour: this cheapest-to-find scene covers barely a third
    assert cloud_cover_frac < 0.9, (
        f"expected the clearest scene to under-cover the AOI, got {cloud_cover_frac:.2f}")


@pytest.mark.network
def test_ranking_is_sorted_descending():
    scenes = search_scenes(SUPERIOR, "2026-08-01", "2026-09-14", limit=8)
    ranked = rank_by_aoi_coverage(scenes, SUPERIOR)
    assert ranked, "expected readable scenes"
    fracs = [f for f, _ in ranked]
    assert fracs == sorted(fracs, reverse=True)
