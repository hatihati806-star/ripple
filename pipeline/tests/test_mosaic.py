"""Mosaic behaviour: best-pixel compositing, majority-voted water, and early stopping.

Each of these was found by inspecting real frames: a naive "newest wins" fold let a cloudy
pass erase clear water from days earlier, a single nearest-sampled class code decided the
water mask of a 2 km cell, and a 42-day candidate set of >2,600 granules was being read in
full when a few hundred saturate the mosaic.
"""

import numpy as np
import pytest

from ripple_pipeline import cli


def scene(scene_id: str, stamp: str) -> dict:
    return {"id": scene_id, "properties": {"datetime": stamp}}


def layers_for(scene_id: str, green_value: int, scl_value: int, shape=(2, 2)):
    return {"green": np.full(shape, green_value, dtype="uint16"),
            "scl": np.full(shape, scl_value, dtype="uint16")}


def test_newer_cloud_does_not_erase_an_older_clear_observation(monkeypatch):
    grid = {"width": 2, "height": 2, "res_m": 1000.0}
    scenes = [scene("new", "2026-09-16T10:00:00Z"), scene("old", "2026-09-10T10:00:00Z")]
    by_scene = {
        "new": layers_for("new", 500, 9),   # SCL cloud_high: unusable
        "old": layers_for("old", 200, 6),   # SCL water
    }
    monkeypatch.setattr(cli, "_read_scene", lambda s, g, a, shape, sub=1: by_scene[s["id"]])

    canvas, water, used = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"), workers=1)
    assert used == 2
    assert canvas["green"][0, 0] == 200
    assert water[0, 0] == pytest.approx(1.0)


def test_newer_clear_observation_wins_over_older_clear(monkeypatch):
    grid = {"width": 1, "height": 1, "res_m": 1000.0}
    scenes = [scene("new", "2026-09-16T10:00:00Z"), scene("old", "2026-09-10T10:00:00Z")]
    by_scene = {"new": layers_for("new", 500, 6, (1, 1)),
                "old": layers_for("old", 200, 6, (1, 1))}
    monkeypatch.setattr(cli, "_read_scene", lambda s, g, a, shape, sub=1: by_scene[s["id"]])

    canvas, _, _ = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"), workers=1)
    assert canvas["green"][0, 0] == 500


def test_a_cell_that_is_mostly_land_is_not_marked_water(monkeypatch):
    """The mask is a majority vote over sub-samples, not one sampled class code."""
    grid = {"width": 2, "height": 2, "res_m": 1000.0}
    scenes = [scene("a", "2026-09-16T10:00:00Z")]
    # 2x2 sub-samples per cell: the first cell is 1/4 water, the second is fully water.
    scl = np.array([[6, 5], [5, 5]], dtype="uint16")
    scl_full = np.array([[6, 6], [6, 6]], dtype="uint16")
    frames = {
        "green": np.full((2, 2), 300, dtype="uint16"),
        "scl": np.block([[scl, np.full((2, 2), 5, dtype="uint16")],
                         [np.full((2, 2), 5, dtype="uint16"), scl_full]]),
    }
    monkeypatch.setattr(cli, "_read_scene", lambda s, g, a, shape, sub=1: frames)

    _, water, _ = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"), workers=1, sub=2)
    assert water[0, 0] == pytest.approx(0.25)
    assert water[1, 1] == pytest.approx(1.0)


def test_mosaic_stops_once_scenes_add_nothing(monkeypatch):
    grid = {"width": 2, "height": 1, "res_m": 1000.0}
    scenes = [scene(f"s{i}", f"2026-09-{16 - i:02d}T10:00:00Z") for i in range(6)]
    reads = {"count": 0}

    def fake_read(s, g, a, shape, sub=1):
        reads["count"] += 1
        return layers_for(s["id"], 299, 6, (1, 2))

    monkeypatch.setattr(cli, "_read_scene", fake_read)

    canvas, _, used = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"),
                                        workers=1, patience=2, max_scenes=6)
    assert used < len(scenes), "expected early stopping"
    assert used <= 4
    assert reads["count"] == used
    assert canvas["green"][0, 0] == 299


def test_progress_threshold_scales_with_the_grid():
    """A scene covers ~110 km. On the continental grid that is 0.03% of the grid, so the
    old fixed 0.02% threshold made almost every real scene look like noise and tripped the
    patience stop after a few hundred scenes per window."""
    small = {"width": 40, "height": 20, "res_m": 4000.0}
    huge = {"width": 4096, "height": 2777, "res_m": 1848.0}
    assert cli.progress_threshold(small) == cli.MIN_GAIN
    assert cli.MIN_GAIN_FLOOR <= cli.progress_threshold(huge) < cli.MIN_GAIN


def test_mosaic_respects_the_hard_scene_cap(monkeypatch):
    grid = {"width": 3, "height": 1, "res_m": 1000.0}
    scenes = [scene(f"s{i}", f"2026-09-{16 - i:02d}T10:00:00Z") for i in range(6)]
    counter = {"n": 0}

    def fake_read(s, g, a, shape, sub=1):
        counter["n"] += 1
        return layers_for(s["id"], 100 + counter["n"], 6, (1, 3))

    monkeypatch.setattr(cli, "_read_scene", fake_read)

    _, _, used = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"),
                                   workers=1, max_scenes=2, patience=99)
    assert used == 2


def test_mosaic_reads_every_scene_when_nothing_is_usable(monkeypatch):
    grid = {"width": 2, "height": 2, "res_m": 1000.0}
    scenes = [scene("a", "2026-09-16T10:00:00Z"), scene("b", "2026-09-15T10:00:00Z")]
    monkeypatch.setattr(cli, "_read_scene",
                        lambda s, g, a, shape, sub=1: layers_for(s["id"], 0, 0))

    canvas, water, used = cli.mosaic_scenes(scenes, grid, assets=("green", "scl"),
                                            workers=1, patience=40)
    assert used == 2
    assert canvas["green"].sum() == 0
    assert water.sum() == 0


def test_shared_pass_reads_overlapping_scenes_once(monkeypatch):
    """A scene in two rolling windows must cost one read, not two."""
    grid = {"width": 2, "height": 1, "res_m": 1000.0}
    shared = scene("shared", "2026-09-10T10:00:00Z")
    older = scene("older", "2026-09-03T10:00:00Z")
    reads: list[str] = []

    def fake_read(s, g, a, shape, sub=1):
        reads.append(s["id"])
        value = 400 if s["id"] == "shared" else 200
        return layers_for(s["id"], value, 6, (1, 2))

    monkeypatch.setattr(cli, "_read_scene", fake_read)
    canvases, _, used = cli.mosaic_windows(
        [[shared, older], [shared]], grid, assets=("green", "scl"),
        workers=1, patience=99, max_scenes=99)

    assert len(canvases) == 2
    assert used[0] == 2 and used[1] == 1
    assert reads.count("shared") == 1, reads
    assert (canvases[0]["green"] == 400).all()
    assert (canvases[1]["green"] == 400).all()


def test_shared_pass_stops_a_saturated_window_without_starving_others(monkeypatch):
    grid = {"width": 2, "height": 1, "res_m": 1000.0}
    early = [scene(f"e{i}", f"2026-09-{16 - i:02d}T10:00:00Z") for i in range(5)]
    late = [scene("late", "2026-09-01T10:00:00Z")]

    def fake_read(s, g, a, shape, sub=1):
        if s["id"] == "late":
            green = np.array([[300, 0]], dtype="uint16")
        else:
            green = np.full((1, 2), 500, dtype="uint16")
        return {"green": green, "scl": np.full((1, 2), 6, dtype="uint16")}

    monkeypatch.setattr(cli, "_read_scene", fake_read)
    canvases, _, used = cli.mosaic_windows(
        [early, late], grid, assets=("green", "scl"),
        workers=1, patience=2, max_scenes=99)

    # Window 0 saturates after the first scene plus patience; it must stop consuming reads.
    assert used[0] <= 3, used
    # Window 1 has its own budget and still gets its scene.
    assert used[1] == 1
    assert (canvases[1]["green"] == np.array([[300, 0]])).all()


def test_a_capped_window_does_not_starve_the_older_ones(monkeypatch):
    """A window that reaches its scene cap leaves thousands of its leftover scenes ahead
    of every older scene in newest-first order. Reading those leftovers spends the whole
    budget with nowhere to put the pixels: on the continental build that burned ~1,650
    reads and three of four observed frames came out empty."""
    grid = {"width": 2, "height": 1, "res_m": 1000.0}
    newest = [scene(f"n{i:02d}", f"2026-09-{16 - (i % 12):02d}T{i:02d}:00:00Z")
              for i in range(20)]
    older = [scene("old", "2026-09-01T10:00:00Z")]
    reads: list[str] = []

    def fake_read(s, g, a, shape, sub=1):
        reads.append(s["id"])
        return layers_for(s["id"], 300, 6, (1, 2))

    monkeypatch.setattr(cli, "_read_scene", fake_read)
    _, _, used = cli.mosaic_windows(
        [older, newest], grid, assets=("green", "scl"),
        workers=2, patience=99, max_scenes=5)

    assert used[0] == 1, used
    assert used[1] == 5, used
    assert reads.count("old") == 1, reads
    assert len(reads) <= 5 * 2, reads
