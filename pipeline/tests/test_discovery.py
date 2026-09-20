import json
import math

import numpy as np
import pytest
from affine import Affine

from ripple_pipeline.waters import (
    CATALOG,
    Body,
    _distance_km,
    body_labels,
    discover_bodies,
    footprint_statistics,
    ground_area_km2,
    load_ocean_mask,
    mercator_to_lonlat,
    region_edge_water,
)

# A small Mercator grid over the US plains: 4 km cells, 40 x 20 cells.
RES_M = 4000.0
LEFT, TOP = -1.05e7, 5.2e6
GRID = {
    "width": 40,
    "height": 20,
    "res_m": RES_M,
    "transform": Affine(RES_M, 0.0, LEFT, 0.0, -RES_M, TOP),
    "merc_bounds": (LEFT, TOP - 20 * RES_M, LEFT + 40 * RES_M, TOP),
}


def blob(rows, cols):
    mask = np.zeros((GRID["height"], GRID["width"]), dtype=bool)
    mask[rows[0]:rows[1], cols[0]:cols[1]] = True
    return mask


def test_catalog_entries_have_unique_ids_and_areas():
    ids = [body.id for body in CATALOG]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 100, "the name index should cover the whole continent"
    for body in CATALOG:
        assert body.area_km2 is None or body.area_km2 > 0


def test_ground_area_corrects_mercator_stretch():
    # One projected square kilometre at 60N is a quarter of a ground square kilometre.
    assert ground_area_km2(1e6, 60.0) == pytest.approx(0.25, rel=1e-3)
    assert ground_area_km2(1e6, 0.0) == pytest.approx(1.0, rel=1e-6)


def test_mercator_round_trip():
    for lon, lat in ((-100.0, 47.0), (-75.0, 25.0), (-120.0, 38.0)):
        x = (lon * math.pi / 180) * 6378137.0
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
        lon_out, lat_out = mercator_to_lonlat(x, y)
        assert lon_out == pytest.approx(lon, abs=1e-6)
        assert lat_out == pytest.approx(lat, abs=1e-6)


def test_discovery_finds_a_large_blob_and_measures_its_area():
    mask = blob((4, 16), (5, 25))  # 12 x 20 cells = 240 cells = 3840 km2 projected
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    assert len(bodies) == 1
    body = bodies[0]
    # Reported area is *ground* area: Mercator inflates a projected area by 1/cos2(lat),
    # so at ~42N the 3840 km2 projected blob is ~2100 km2 of actual lake.
    expected = 240 * 16.0 * math.cos(math.radians(body.lat)) ** 2
    assert body.area_km2 == pytest.approx(expected, rel=0.02)
    assert body.area_km2 < 240 * 16.0
    assert -105.0 < body.lon < -80.0
    assert 35.0 < body.lat < 50.0
    assert body.curated is False
    assert "Water body" in body.name


def test_discovery_ignores_patches_below_the_threshold():
    mask = blob((4, 6), (5, 7))  # 2 x 2 cells = 16 km2
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    assert bodies == []


def test_discovery_orders_largest_first_and_respects_the_cap():
    mask = blob((1, 19), (1, 39))
    mask[2:5, 2:6] = False
    mask[10:15, 30:38] = False
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=1, curated=())
    assert len(bodies) == 1
    assert bodies[0].area_km2 > 1000


def test_discovery_names_bodies_from_the_catalog_when_close():
    mask = np.zeros((GRID["height"], GRID["width"]), dtype=bool)
    mask[4:10, 5:15] = True
    centroid_merc = (
        LEFT + 10 * RES_M,
        TOP - 7 * RES_M,
    )
    lon, lat = mercator_to_lonlat(*centroid_merc)
    curated = (Body("test-lake", "Test Lake", "lake", lat, lon),)
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=5, curated=curated)
    assert len(bodies) == 1
    assert bodies[0].name == "Test Lake"
    assert bodies[0].id == "test-lake"
    assert bodies[0].curated is True


def test_discovery_keeps_two_distant_blobs_separate():
    mask = blob((2, 6), (2, 10))
    mask[12:18, 28:36] = True
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=10, curated=())
    assert len(bodies) == 2


def test_a_catalog_point_inside_a_large_patch_names_it():
    """Catalog points are open-water points, not centroids: Lake Superior's centroid is
    31 km from its catalog point, and a centroid-distance test left the largest lake in
    the region named by its coordinates."""
    mask = blob((4, 20), (5, 35))
    lon, lat = mercator_to_lonlat(LEFT + 6 * RES_M, TOP - 6 * RES_M)
    centroid_lon, centroid_lat = mercator_to_lonlat(
        LEFT + 20 * RES_M, TOP - 12 * RES_M)
    assert _distance_km(lon, lat, centroid_lon, centroid_lat) > 25.0, "fixture too close"
    curated = (Body("big-lake", "Big Lake", "lake", lat, lon),)
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=5, curated=curated)
    assert bodies[0].name == "Big Lake"
    assert bodies[0].curated is True


def test_a_catalog_point_far_outside_a_patch_is_not_claimed():
    mask = blob((4, 8), (4, 12))
    lon, lat = mercator_to_lonlat(LEFT + 34 * RES_M, TOP - 6 * RES_M)  # ~85 km away
    curated = (Body("far-lake", "Far Lake", "lake", lat, lon),)
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=5, curated=curated)
    assert bodies[0].curated is False
    assert "Water body" in bodies[0].name


def test_region_edge_water_excludes_border_patches_and_keeps_inland_lakes():
    mask = np.zeros((GRID["height"], GRID["width"]), dtype=bool)
    mask[0:5, :] = True          # an "ocean" along the top border
    mask[:, 0:3] = True          # and along the left
    mask[10:14, 20:28] = True    # an inland lake, far from every border
    edge = region_edge_water(mask, GRID)
    assert edge[0, 0] and edge[2, 30]
    assert not edge[12, 24]
    kept = mask & ~edge
    assert kept[12, 24] and not kept[2, 30]


def test_region_edge_water_returns_nothing_when_every_patch_is_inland():
    mask = blob((8, 12), (8, 16))
    assert not region_edge_water(mask, GRID).any()


def test_load_ocean_mask_rasterizes_the_cached_geojson(tmp_path):
    lon0, lat0 = mercator_to_lonlat(LEFT, TOP - 20 * RES_M)
    lon1, lat1 = mercator_to_lonlat(LEFT + 20 * RES_M, TOP)
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0],
            ]]},
        }],
    }
    cache = tmp_path / "ocean.geojson"
    cache.write_text(json.dumps(geojson), encoding="utf-8")

    ocean = load_ocean_mask(GRID, cache_path=cache)
    assert ocean.shape == (GRID["height"], GRID["width"])
    assert ocean[0, 0]
    assert ocean[:, :15].any()
    assert not ocean[:, 25:].any()


def test_load_ocean_mask_is_empty_when_the_file_is_missing(tmp_path):
    ocean = load_ocean_mask(GRID, cache_path=tmp_path / "absent.geojson")
    assert ocean.shape == (GRID["height"], GRID["width"])
    assert not ocean.any()


def test_the_closest_catalog_point_wins_when_two_share_a_patch():
    """A big patch whose footprint contains two catalog points must take the name of the
    lake it actually is, not whichever entry happens to come first in the tuple."""
    mask = blob((4, 20), (5, 35))
    far_lon, far_lat = mercator_to_lonlat(LEFT + 6 * RES_M, TOP - 6 * RES_M)
    near_lon, near_lat = mercator_to_lonlat(LEFT + 21 * RES_M, TOP - 12 * RES_M)
    curated = (Body("far-lake", "Far Lake", "lake", far_lat, far_lon),
               Body("near-lake", "Near Lake", "lake", near_lat, near_lon))
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=5, curated=curated)
    assert bodies[0].name == "Near Lake"
    assert bodies[0].id == "near-lake"


def test_a_catalog_name_is_used_once_and_goes_to_the_largest_polygon():
    """A lake split by a causeway must not produce two rows with the same name."""
    mask = blob((3, 8), (3, 12))       # ~45 cells
    mask[3:9, 13:25] = True            # ~72 cells, same catalogue entry, also in range
    lon, lat = mercator_to_lonlat(LEFT + 13 * RES_M, TOP - 6 * RES_M)
    curated = (Body("split-lake", "Split Lake", "lake", lat, lon),)
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=10, curated=curated)
    named = [body for body in bodies if body.curated]
    assert len(named) == 1, [body.name for body in bodies]
    assert named[0].area_km2 == max(body.area_km2 for body in bodies)
    assert all("Water body" in body.name for body in bodies if not body.curated)


# ---------------------------------------------------------------------------------------
# Footprints: a body's reading is measured over its own area, not at one cell
# ---------------------------------------------------------------------------------------
def test_discovery_keeps_each_body_geometry():
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    assert bodies[0].geometry is not None
    assert bodies[0].geometry["type"] in {"Polygon", "MultiPolygon"}


def test_body_labels_rasterise_the_footprint():
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    labels, footprint = body_labels(bodies, GRID)
    assert labels.shape == (GRID["height"], GRID["width"])
    assert labels.max() == 1
    # 12 x 20 cells of mask, polygonised and filled back in.
    assert footprint[0] == pytest.approx(240, abs=2)
    assert int((labels == 1).sum()) == footprint[0]


def test_body_labels_separate_two_bodies():
    mask = blob((2, 6), (2, 10))
    mask[12:18, 28:36] = True
    bodies = discover_bodies(mask, GRID, min_area_km2=50.0, max_bodies=10, curated=())
    labels, footprint = body_labels(bodies, GRID)
    assert labels.max() == 2
    assert all(count > 0 for count in footprint)


def test_body_labels_are_empty_without_geometry():
    labels, footprint = body_labels([Body("x", "X", "lake", 40.0, -100.0)], GRID)
    assert not labels.any()
    assert list(footprint) == [0]


def test_footprint_statistics_averages_over_the_whole_body():
    """The point of the change: a hot cell must not become the body's headline."""
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    labels, footprint = body_labels(bodies, GRID)

    values = np.zeros((GRID["height"], GRID["width"]), dtype="float32")
    values[mask] = 0.2
    values[10, 12] = 1.0                      # one saturated cell
    valid = mask.copy()

    stats = footprint_statistics(labels, values, valid, len(bodies))[0]
    assert stats["cells"] == int(footprint[0])
    assert stats["peak"] == pytest.approx(1.0)
    assert 0.2 < stats["mean"] < 0.21, stats["mean"]
    assert stats["std"] > 0.0


def test_footprint_statistics_ignore_cells_outside_the_water_mask():
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    labels, footprint = body_labels(bodies, GRID)

    values = np.full((GRID["height"], GRID["width"]), 0.9, dtype="float32")
    valid = np.zeros_like(mask)
    valid[6:8, 6:8] = True                    # four cells of the body carry data
    stats = footprint_statistics(labels, values, valid, len(bodies))[0]
    assert stats["cells"] == 4
    assert stats["mean"] == pytest.approx(0.9)
    assert stats["cells"] < footprint[0], "the footprint is bigger than the sample"


def test_footprint_statistics_report_nothing_when_no_cell_is_valid():
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    labels, _ = body_labels(bodies, GRID)
    stats = footprint_statistics(labels, np.zeros_like(mask, dtype="float32"),
                                 np.zeros_like(mask), len(bodies))[0]
    assert stats == {"cells": 0, "mean": None, "peak": None, "std": None}


def test_footprint_statistics_skip_nan_cells_without_losing_the_peak():
    mask = blob((4, 16), (5, 25))
    bodies = discover_bodies(mask, GRID, min_area_km2=100.0, max_bodies=10, curated=())
    labels, _ = body_labels(bodies, GRID)
    values = np.full((GRID["height"], GRID["width"]), np.nan, dtype="float32")
    values[6, 6] = 0.4
    values[6, 7] = np.nan          # inside the footprint, no reading
    values[6, 8] = 0.8
    stats = footprint_statistics(labels, values, mask.copy(), len(bodies))[0]
    assert stats["cells"] == 2, "a NaN cell is not a reading"
    assert stats["mean"] == pytest.approx(0.6)
    assert stats["peak"] == pytest.approx(0.8)


def test_footprint_statistics_reject_mismatched_shapes():
    labels = np.zeros((4, 4), dtype="int32")
    with pytest.raises(ValueError):
        footprint_statistics(labels, np.zeros((3, 3), dtype="float32"),
                             np.zeros((4, 4), dtype=bool), 1)
