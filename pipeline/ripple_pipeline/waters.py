"""Water bodies: a curated name index plus discovery from the data itself.

Two things live here, and the difference matters:

* **The catalog** is a *naming and matching index* -- coordinates of well-known lakes so a
  discovered body can be labelled. It is deliberately not the list of what gets covered;
  a hand-written list of 29 lakes was the previous limitation.
* **Discovery** finds bodies in the composited water mask: the mask is sieved to remove
  patches below the area threshold, polygonised, and each surviving polygon is measured
  (area, centroid) in the pipeline's own projection. Coverage therefore comes from the
  satellite data, not from an assumption about which lakes matter.

Area is reported in ground square kilometres. Mercator is conformal, so a polygon's
projected area is inflated by 1/cos²(latitude); the correction is applied at the centroid.
"""
from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rasterio import features
from rasterio.warp import transform as transform_points
from rasterio.warp import transform_geom

WEB_MERCATOR = "EPSG:3857"

# Key-free coastline reference used to take the ocean out of the analysis. Natural Earth's
# ocean layer is a static, public-domain polygon set; the pipeline downloads it once and
# caches the file next to the package.
OCEAN_GEOJSON_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
                     "master/geojson/ne_10m_ocean.geojson")
OCEAN_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# A catalog coordinate that sits a kilometre or two off open water still resolves, without
# the search reaching a neighbouring lake. Expressed in metres rather than cells so the
# behaviour does not change with grid resolution -- at 652 m a fixed 3-cell radius is 2 km,
# which silently fails for half the catalog.
DEFAULT_SEARCH_M = 12000.0


@dataclass(frozen=True)
class Body:
    id: str
    name: str
    kind: str
    lat: float
    lon: float
    area_km2: float | None = None
    curated: bool = False
    # GeoJSON geometry in the grid's own CRS, as returned by ``features.shapes``. Kept so
    # a body can be turned into a footprint mask and measured over its whole area rather
    # than at one sampled cell (see ``body_labels``).
    geometry: dict | None = None
    # A property of the water that makes an optical index unreliable there, stated in the
    # UI rather than silently averaged away. See ``CAUTION_HYPERSALINE``.
    caution: str | None = None


CAUTION_HYPERSALINE = (
    "Hypersaline: mineral and halophilic colour dominates the spectrum, so the turbidity "
    "index is not a sediment proxy here"
)


# Name index for the continental region: US, southern Canada, Mexico. Coordinates are
# approximate open-water points; discovery supplies the real geometry and area.
CATALOG: tuple[Body, ...] = (
    # Great Lakes and eastern Canada
    Body("lake-superior", "Lake Superior", "lake", 47.70, -87.50, 82100),
    Body("lake-michigan", "Lake Michigan", "lake", 44.00, -86.85, 58000),
    Body("lake-huron", "Lake Huron", "lake", 44.80, -82.40, 59600),
    Body("lake-erie", "Lake Erie", "lake", 41.90, -81.20, 25700),
    Body("lake-ontario", "Lake Ontario", "lake", 43.60, -77.90, 18960),
    Body("lake-st-clair", "Lake St. Clair", "lake", 42.42, -82.72, 1114),
    Body("georgian-bay", "Georgian Bay", "bay", 45.20, -80.80, 15000),
    Body("lake-simcoe", "Lake Simcoe", "lake", 44.42, -79.40, 722),
    Body("lake-nipissing", "Lake Nipissing", "lake", 46.30, -79.80, 873),
    Body("lake-timiskaming", "Lake Timiskaming", "lake", 47.00, -79.50, 295),
    Body("lake-nipigon", "Lake Nipigon", "lake", 49.83, -88.50, 4848),
    Body("lac-seul", "Lac Seul", "lake", 50.30, -92.50, 1657),
    Body("lake-st-joseph", "Lake St. Joseph", "lake", 51.05, -90.50, 493),
    Body("lac-mistassini", "Lac Mistassini", "lake", 51.00, -73.70, 2335),
    Body("lac-saint-jean", "Lac Saint-Jean", "lake", 48.60, -72.00, 1003),
    Body("reservoir-gouin", "Reservoir Gouin", "reservoir", 48.40, -74.20, 1570),
    Body("lake-of-the-woods", "Lake of the Woods", "lake", 49.30, -94.90, 4350),
    Body("rainy-lake", "Rainy Lake", "lake", 48.60, -93.10, 932),
    Body("lake-winnipeg", "Lake Winnipeg", "lake", 52.10, -97.30, 24400),
    Body("lake-manitoba", "Lake Manitoba", "lake", 50.90, -98.60, 4624),
    Body("lake-winnipegosis", "Lake Winnipegosis", "lake", 52.40, -100.00, 5374),
    Body("cedar-lake", "Cedar Lake", "lake", 53.30, -100.30, 1353),
    Body("dauphin-lake", "Dauphin Lake", "lake", 51.28, -99.75, 519),
    Body("lake-of-the-prairies", "Lake of the Prairies", "reservoir", 50.95, -101.50, 66),
    Body("last-mountain-lake", "Last Mountain Lake", "reservoir", 51.10, -105.20, 231),
    Body("lake-diefenbaker", "Lake Diefenbaker", "reservoir", 51.05, -106.60, 430),
    # Upper Midwest and Great Plains reservoirs
    Body("lake-winnebago", "Lake Winnebago", "lake", 43.95, -88.42, 557),
    Body("green-bay", "Green Bay", "bay", 44.90, -87.60, 4200),
    Body("grand-traverse-bay", "Grand Traverse Bay", "bay", 44.90, -85.50, 770),
    Body("saginaw-bay", "Saginaw Bay", "bay", 43.85, -83.80, 2960),
    Body("lake-mendota", "Lake Mendota", "lake", 43.10, -89.40, 39),
    Body("mille-lacs", "Mille Lacs Lake", "lake", 46.25, -93.65, 536),
    Body("leech-lake", "Leech Lake", "lake", 47.15, -94.40, 449),
    Body("winnibigoshish", "Lake Winnibigoshish", "lake", 47.45, -94.20, 231),
    Body("red-lake", "Red Lake", "lake", 47.90, -94.90, 1148),
    Body("lake-pepin", "Lake Pepin", "lake", 44.50, -92.35, 103),
    Body("devils-lake", "Devils Lake", "lake", 47.95, -98.90, 416),
    Body("lake-sakakawea", "Lake Sakakawea", "reservoir", 47.65, -102.30, 1560),
    Body("lake-oahe", "Lake Oahe", "reservoir", 45.00, -100.30, 1500),
    Body("lake-sharpe", "Lake Sharpe", "reservoir", 44.30, -99.50, 240),
    Body("lake-francis-case", "Lake Francis Case", "reservoir", 43.30, -98.90, 410),
    Body("lewis-and-clark-lake", "Lewis and Clark Lake", "reservoir", 42.87, -97.50, 130),
    Body("lake-mcconaughy", "Lake McConaughy", "reservoir", 41.30, -101.80, 145),
    Body("fort-peck", "Fort Peck Lake", "reservoir", 47.75, -106.40, 980),
    Body("lake-sakakawea-west", "Lake Audubon", "reservoir", 47.65, -101.40, 70),
    # Mountain west
    Body("flathead-lake", "Flathead Lake", "lake", 47.90, -114.10, 496),
    Body("lake-coeur-dalene", "Lake Coeur d'Alene", "lake", 47.70, -116.80, 130),
    Body("yellowstone-lake", "Yellowstone Lake", "lake", 44.40, -110.40, 350),
    Body("bear-lake", "Bear Lake", "lake", 42.00, -111.30, 282),
    Body("great-salt-lake", "Great Salt Lake", "lake", 41.20, -112.50, 4400,
         caution=CAUTION_HYPERSALINE),
    Body("utah-lake", "Utah Lake", "lake", 40.20, -111.80, 380),
    Body("lake-tahoe", "Lake Tahoe", "lake", 39.10, -120.00, 495),
    Body("pyramid-lake", "Pyramid Lake", "lake", 40.00, -119.60, 488),
    Body("mono-lake", "Mono Lake", "lake", 38.00, -119.00, 180),
    Body("salton-sea", "Salton Sea", "lake", 33.30, -115.80, 888),
    Body("lake-mead", "Lake Mead", "reservoir", 36.10, -114.40, 640),
    Body("lake-powell", "Lake Powell", "reservoir", 37.00, -111.20, 653),
    Body("flaming-gorge", "Flaming Gorge Reservoir", "reservoir", 41.00, -109.50, 170),
    Body("lake-rio-grande", "Cochiti Lake", "reservoir", 35.63, -106.30, 50),
    Body("elephant-butte", "Elephant Butte Lake", "reservoir", 33.20, -107.20, 150),
    # Plains and Texas
    Body("lake-texoma", "Lake Texoma", "reservoir", 33.90, -96.70, 360),
    Body("toledo-bend", "Toledo Bend Reservoir", "reservoir", 31.50, -93.70, 749),
    Body("sam-rayburn", "Sam Rayburn Reservoir", "reservoir", 31.10, -94.10, 460),
    Body("lake-livingston", "Lake Livingston", "reservoir", 30.60, -95.10, 335),
    Body("caddo-lake", "Caddo Lake", "lake", 32.70, -94.10, 106),
    Body("lake-of-the-ozarks", "Lake of the Ozarks", "reservoir", 38.20, -92.70, 220),
    Body("table-rock-lake", "Table Rock Lake", "reservoir", 36.60, -93.30, 180),
    Body("bull-shoals", "Bull Shoals Lake", "reservoir", 36.40, -92.60, 180),
    Body("grand-lake-o-cherokees", "Grand Lake o' the Cherokees", "reservoir", 36.60, -94.90, 190),
    # Mississippi and Ohio valleys
    Body("kentucky-lake", "Kentucky Lake", "reservoir", 36.60, -88.10, 650),
    Body("lake-barkley", "Lake Barkley", "reservoir", 36.70, -87.90, 230),
    Body("lake-cumberland", "Lake Cumberland", "reservoir", 36.90, -85.20, 255),
    Body("norris-lake", "Norris Lake", "reservoir", 36.30, -83.90, 138),
    Body("lake-watts-bar", "Watts Bar Lake", "reservoir", 35.70, -84.70, 160),
    # Southeast
    Body("lake-marion", "Lake Marion", "reservoir", 33.50, -80.40, 450),
    Body("lake-moultrie", "Lake Moultrie", "reservoir", 33.30, -80.10, 240),
    Body("lake-murray", "Lake Murray", "reservoir", 34.10, -81.40, 200),
    Body("lake-norman", "Lake Norman", "reservoir", 35.50, -80.90, 130),
    Body("smith-mountain-lake", "Smith Mountain Lake", "reservoir", 37.10, -79.60, 83),
    Body("kerr-lake", "Kerr Lake", "reservoir", 36.60, -78.60, 200),
    Body("lake-okeechobee", "Lake Okeechobee", "lake", 26.90, -80.90, 1890),
    Body("lake-kissimmee", "Lake Kissimmee", "lake", 27.90, -81.30, 140),
    Body("lake-apopka", "Lake Apopka", "lake", 28.60, -81.60, 125),
    Body("lake-george-fl", "Lake George", "lake", 29.30, -81.60, 190),
    Body("lake-pontchartrain", "Lake Pontchartrain", "lake", 30.20, -90.10, 1630),
    # Northeast and Mid-Atlantic
    Body("lake-champlain", "Lake Champlain", "lake", 44.50, -73.30, 1130),
    Body("lake-george-ny", "Lake George", "lake", 43.50, -73.60, 114),
    Body("oneida-lake", "Oneida Lake", "lake", 43.20, -75.80, 207),
    Body("cayuga-lake", "Cayuga Lake", "lake", 42.70, -76.70, 172),
    Body("seneca-lake", "Seneca Lake", "lake", 42.70, -76.90, 175),
    Body("lake-winnipesaukee", "Lake Winnipesaukee", "lake", 43.60, -71.30, 186),
    Body("sebago-lake", "Sebago Lake", "lake", 43.80, -70.50, 122),
    Body("moosehead-lake", "Moosehead Lake", "lake", 45.60, -69.70, 300),
    Body("lake-ontario-east", "Upper Rideau Lake", "lake", 44.70, -76.30, 26),
    # Mexico
    Body("lake-chapala", "Lake Chapala", "lake", 20.25, -103.00, 1148),
    Body("lake-patzcuaro", "Lake Patzcuaro", "lake", 19.60, -101.60, 130),
    Body("lake-cuitzeo", "Lake Cuitzeo", "lake", 19.95, -101.10, 300),
    Body("lake-yuriria", "Lake Yuriria", "lake", 20.25, -101.10, 80),
    Body("presas-del-llano", "Presa del Llano", "reservoir", 19.90, -99.90, 30),
    Body("lake-valle-de-bravo", "Lake Valle de Bravo", "reservoir", 19.20, -100.15, 20),
    Body("presa-chapulhuacan", "Presa Requena", "reservoir", 19.85, -99.35, 25),
    Body("lake-tequesquitengo", "Lake Tequesquitengo", "lake", 18.62, -99.26, 8),
    Body("presa-infiernillo", "Presa Infiernillo", "reservoir", 18.20, -101.90, 300),
    Body("lake-catemaco", "Lake Catemaco", "lake", 18.42, -95.10, 72),
)


def load_ocean_mask(grid: dict, cache_path: Path | None = None,
                    url: str = OCEAN_GEOJSON_URL) -> np.ndarray:
    """Boolean ocean mask on the analysis grid, from Natural Earth's ocean polygons.

    The ocean is water, but it is not a lake: a Gulf of Mexico fragment measured 193,000
    km2 and outranked every real lake, and a coastal fragment claimed the name "Lake
    Marion". The ocean is also fragmented in the mosaic (cloud and nodata gaps), so it
    cannot be recognised by touching the region border -- the fragments sit hundreds of
    kilometres short of it. A static coastline is the honest way to separate the two.

    The download happens once and is cached; if the file is unavailable the function
    returns an all-False mask so the pipeline still runs, and the geometric border rule in
    ``region_edge_water`` remains as a fallback.
    """
    cache = Path(cache_path) if cache_path is not None else OCEAN_CACHE_DIR / "ne_10m_ocean.geojson"
    try:
        if not cache.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=120) as response:
                cache.write_bytes(response.read())
        payload = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return np.zeros((grid["height"], grid["width"]), dtype=bool)

    geometries = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            geometries.append(transform_geom("EPSG:4326", WEB_MERCATOR, geometry))
        except Exception:
            continue
    if not geometries:
        return np.zeros((grid["height"], grid["width"]), dtype=bool)

    raster = features.rasterize(
        geometries, out_shape=(grid["height"], grid["width"]),
        transform=grid["transform"], fill=0, default_value=1, dtype="uint8")
    return raster.astype(bool)


def region_edge_water(mask: np.ndarray, grid: dict,
                      min_pixels: int = 6) -> np.ndarray:
    """Water patches connected to the analysis-region border: ocean, gulfs, estuaries.

    They are water, but they are not lakes, and their measured extent is an artifact of
    where the analysis box was drawn: the truncated Pacific patch measured 163,000 km2 on
    the continental grid and outranked every real lake in the catalog. Inland water does
    not reach the border, so patches that touch it are dropped from the mask, the display
    and the body list. A patch that merely *contains* the region border is excluded; a
    lake that only touches a border cell because the grid clipped it is not, because the
    region is drawn well outside every catalog lake.
    """
    m = np.ascontiguousarray(mask.astype("uint8"))
    sieved = features.sieve(m, size=min_pixels, connectivity=8)
    transform = grid["transform"]
    x_min, y_max = transform * (0, 0)
    x_max, y_min = transform * (grid["width"], grid["height"])
    margin = 2.0 * grid["res_m"]

    touching = []
    for geometry, value in features.shapes(sieved, mask=sieved.astype(bool),
                                           transform=transform, connectivity=8):
        if value != 1:
            continue
        polys = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                 else geometry["coordinates"])
        xs = [point[0] for poly in polys for ring in poly for point in ring]
        ys = [point[1] for poly in polys for ring in poly for point in ring]
        if (min(xs) <= x_min + margin or max(xs) >= x_max - margin
                or min(ys) <= y_min + margin or max(ys) >= y_max - margin):
            touching.append(geometry)

    if not touching:
        return np.zeros(mask.shape, dtype=bool)
    edge = features.rasterize(touching, out_shape=mask.shape, transform=transform,
                              fill=0, default_value=1, dtype="uint8")
    return edge.astype(bool)


def search_radius_cells(res_m: float, search_m: float = DEFAULT_SEARCH_M) -> int:
    """Search radius in grid cells covering ``search_m``, at least 3 cells."""
    return max(3, int(np.ceil(float(search_m) / max(1e-6, float(res_m)))))


def body_rc(grid: dict, body) -> tuple[int, int] | None:
    """Grid (row, col) holding the body's coordinate, or None when outside the grid."""
    (x, y), = zip(*transform_points("EPSG:4326", WEB_MERCATOR, [body.lon], [body.lat]))
    inverse = ~grid["transform"]
    col_f, row_f = inverse * (x, y)
    row, col = int(round(row_f)), int(round(col_f))
    if 0 <= row < grid["height"] and 0 <= col < grid["width"]:
        return row, col
    return None


def nearest_valid(valid: np.ndarray, row: int, col: int,
                  radius_cells: int = 3) -> tuple[int, int] | None:
    """Nearest true cell to (row, col) within a square window, or None."""
    height, width = valid.shape
    r0, r1 = max(0, row - radius_cells), min(height, row + radius_cells + 1)
    c0, c1 = max(0, col - radius_cells), min(width, col + radius_cells + 1)
    window = valid[r0:r1, c0:c1]
    if not window.any():
        return None

    rows, cols = np.nonzero(window)
    dr = rows + r0 - row
    dc = cols + c0 - col
    best = int(np.argmin(dr * dr + dc * dc))
    return int(rows[best] + r0), int(cols[best] + c0)


def sample_body(risk: np.ndarray, ndci: np.ndarray, ndti: np.ndarray,
                valid: np.ndarray, body, row: int, col: int,
                radius_cells: int = 3, water_fraction: np.ndarray | None = None) -> dict:
    """One body's values for one frame.

    Among valid cells within the radius, the one holding the *most* water is chosen when a
    water-fraction raster is supplied: the nearest water pixel to a reservoir's centroid is
    often a mixed shoreline cell, and the most-water cell is the honest reading.
    """
    height, width = valid.shape
    r0, r1 = max(0, row - radius_cells), min(height, row + radius_cells + 1)
    c0, c1 = max(0, col - radius_cells), min(width, col + radius_cells + 1)
    window_valid = valid[r0:r1, c0:c1]
    if not window_valid.any():
        return {"risk": None, "ndci": None, "ndti": None, "valid": False,
                "offset_cells": None, "water_fraction": None}

    rows, cols = np.nonzero(window_valid)
    dr = rows + r0 - row
    dc = cols + c0 - col
    if water_fraction is not None:
        fraction = water_fraction[r0:r1, c0:c1][window_valid]
        # Rank by water fraction first (a 0.9 cell beats a nearer 0.3 cell), then distance.
        order = np.lexsort((dr * dr + dc * dc, -fraction))
        best = int(order[0])
    else:
        best = int(np.argmin(dr * dr + dc * dc))

    r = int(rows[best] + r0)
    c = int(cols[best] + c0)
    return {
        "risk": round(float(risk[r, c]), 4),
        "ndci": round(float(ndci[r, c]), 4),
        "ndti": round(float(ndti[r, c]), 4),
        "valid": True,
        "offset_cells": int(max(abs(r - row), abs(c - col))),
        "water_fraction": round(float(water_fraction[r, c]), 3)
        if water_fraction is not None else None,
    }


def _ring_area_centroid(ring) -> tuple[float, float, float]:
    """Signed area and centroid of a closed ring, by the shoelace formula."""
    area = 0.0
    cx = 0.0
    cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area *= 0.5
    if abs(area) < 1e-9:
        return 0.0, float(ring[0][0]), float(ring[0][1])
    return area, cx / (6.0 * area), cy / (6.0 * area)


def _polygon_area_centroid(polygon) -> tuple[float, float, float]:
    """Signed area and centroid of a polygon (outer ring then holes)."""
    total = 0.0
    cx = 0.0
    cy = 0.0
    for ring in polygon:
        area, rx, ry = _ring_area_centroid(ring)
        total += area
        cx += rx * area
        cy += ry * area
    if abs(total) < 1e-9:
        return 0.0, float(polygon[0][0][0]), float(polygon[0][0][1])
    return total, cx / total, cy / total


def mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = (x / 6378137.0) * (180.0 / math.pi)
    lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
    return lon, lat


def ground_area_km2(mercator_area: float, lat: float) -> float:
    """Convert a projected Mercator area to ground area at a given latitude."""
    return mercator_area * math.cos(math.radians(lat)) ** 2 / 1e6


def discover_bodies(valid: np.ndarray, grid: dict, min_area_km2: float,
                    max_bodies: int, curated: tuple[Body, ...] = CATALOG,
                    match_km: float = 25.0) -> list[Body]:
    """Find water bodies in a composited mask, largest first.

    Patches smaller than ``min_area_km2`` are sieved out *before* polygonising, so a region
    with a hundred thousand ponds does not produce a hundred thousand polygons. Each
    surviving polygon is measured in projected space and corrected to ground area, then
    named from the catalog when one is close enough -- otherwise it keeps its coordinates,
    because inventing a name for a lake is worse than describing where it is. A catalog
    point counts as close when it lies inside the patch's footprint or within ``match_km``
    of it (see ``_distance_to_bounds_km``).
    """
    mask = np.ascontiguousarray(valid.astype("uint8"))
    pixel_km2 = (grid["res_m"] / 1000.0) ** 2
    min_pixels = max(4, int(round(min_area_km2 / pixel_km2)))

    sieved = features.sieve(mask, size=min_pixels, connectivity=8)
    polygons: list[tuple[float, float, float, float, float, float, float, dict]] = []
    for geometry, value in features.shapes(
            sieved, mask=sieved.astype(bool), transform=grid["transform"], connectivity=8):
        if value != 1:
            continue
        rings = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                 else geometry["coordinates"])
        total = 0.0
        mx = 0.0
        my = 0.0
        xs: list[float] = []
        ys: list[float] = []
        for poly in rings:
            area, x, y = _polygon_area_centroid(poly)
            total += area
            mx += x * area
            my += y * area
            xs.extend(point[0] for ring in poly for point in ring)
            ys.extend(point[1] for ring in poly for point in ring)
        if abs(total) < 1e-6:
            continue
        mx /= total
        my /= total
        lon, lat = mercator_to_lonlat(mx, my)
        lon_min, lat_min = mercator_to_lonlat(min(xs), min(ys))
        lon_max, lat_max = mercator_to_lonlat(max(xs), max(ys))
        area_km2 = ground_area_km2(abs(total), lat)
        if area_km2 < min_area_km2:
            continue
        polygons.append((area_km2, lon, lat, lon_min, lat_min, lon_max, lat_max,
                         geometry))

    # Largest first, so a catalog name goes to the biggest polygon near its coordinate and
    # the area cap keeps the biggest bodies. A lake split by a causeway or a salt flat
    # otherwise produces several polygons that each claim the same name.
    polygons.sort(key=lambda item: -item[0])
    used_curated: set[str] = set()
    bodies: list[Body] = []
    for area_km2, lon, lat, lon_min, lat_min, lon_max, lat_max, geometry in polygons:
        best_name = None
        best = (match_km, float("inf"))
        body_id = f"w{lon:.2f}_{lat:.2f}".replace("-", "m").replace(".", "p")
        kind = "lake"
        curated_flag = False
        caution = None
        for candidate in curated:
            if candidate.id in used_curated:
                continue
            to_patch = _distance_to_bounds_km(candidate.lon, candidate.lat,
                                              lon_min, lat_min, lon_max, lat_max)
            to_centroid = _distance_km(lon, lat, candidate.lon, candidate.lat)
            if (to_patch, to_centroid) < best:
                best = (to_patch, to_centroid)
                best_name = candidate.name
                kind = candidate.kind
                body_id = candidate.id
                curated_flag = True
                caution = candidate.caution
        if best_name is None:
            hemisphere_lat = "N" if lat >= 0 else "S"
            hemisphere_lon = "E" if lon >= 0 else "W"
            best_name = (f"Water body · {abs(lat):.1f}°{hemisphere_lat} "
                         f"{abs(lon):.1f}°{hemisphere_lon}")
        elif curated_flag:
            used_curated.add(body_id)
        bodies.append(Body(body_id, best_name, kind, lat, lon, round(area_km2, 1),
                           curated_flag, geometry=geometry, caution=caution))
        if len(bodies) >= max_bodies:
            break
    return bodies

def body_labels(bodies: list[Body], grid: dict) -> tuple[np.ndarray, np.ndarray]:
    """Rasterise every body's footprint onto the grid.

    Returns ``(labels, footprint)``: an int32 image where 0 is background and ``i + 1`` is
    ``bodies[i]``, plus the number of grid cells each body's polygon covers. Sampling a
    body at one cell is what made a 4,861 km2 bay's headline number the value of a single
    1.85 km pixel; a footprint mask is what lets a frame be averaged over the body itself.
    """
    count = len(bodies)
    shapes = [(body.geometry, index + 1) for index, body in enumerate(bodies)
              if body.geometry]
    if not shapes:
        empty = np.zeros((grid["height"], grid["width"]), dtype="int32")
        return empty, np.zeros(count, dtype="int64")

    labels = features.rasterize(
        shapes, out_shape=(grid["height"], grid["width"]),
        transform=grid["transform"], fill=0, dtype="int32", all_touched=False)
    footprint = np.bincount(labels.ravel(), minlength=count + 1)[1:count + 1]
    return labels, footprint.astype("int64")


def footprint_statistics(labels: np.ndarray, values: np.ndarray, valid: np.ndarray,
                         count: int) -> list[dict]:
    """Per-body statistics of ``values`` over the cells inside its footprint.

    ``mean`` is the body's areal mean -- the headline reading. ``peak`` is kept because it
    is the single worst cell, which is what a point sample used to report and what a
    reader may want to see next to the mean; the two together are also what tells a
    reader whether the mean is representative.

    Everything is computed over the frame's water mask, which is ~1% of the grid: the
    continental grid is 16.7 M cells per frame, and only ~180 k of them carry a reading.
    """
    shape = labels.shape
    if values.shape != shape or valid.shape != shape:
        raise ValueError("labels, values and valid must share one shape")

    selected = valid & (labels > 0)
    flat_labels = labels[selected]
    flat_values = values[selected]
    finite = np.isfinite(flat_values)

    # "Cells" means cells carrying a reading, not cells inside the polygon: a body whose
    # footprint is 4,000 cells but which was cloud-free on 40 of them must not look like a
    # 4,000-cell measurement.
    cells = np.bincount(flat_labels[finite], minlength=count + 1).astype("int64")
    total = np.bincount(flat_labels[finite], weights=flat_values[finite],
                        minlength=count + 1)
    square = np.bincount(flat_labels[finite], weights=flat_values[finite] ** 2,
                         minlength=count + 1)
    peak = np.full(count + 1, -np.inf, dtype="float64")
    if finite.any():
        np.maximum.at(peak, flat_labels[finite], flat_values[finite])

    out: list[dict] = []
    for index in range(1, count + 1):
        n = int(cells[index])
        if n == 0:
            out.append({"cells": 0, "mean": None, "peak": None, "std": None})
            continue
        mean = total[index] / n
        variance = max(0.0, square[index] / n - mean * mean)
        out.append({
            "cells": n,
            "mean": round(float(mean), 4),
            "peak": round(float(peak[index]), 4) if np.isfinite(peak[index]) else None,
            "std": round(float(math.sqrt(variance)), 4),
        })
    return out


def _distance_km(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    mean_lat = math.radians((lat_a + lat_b) / 2)
    dx = (lon_b - lon_a) * math.cos(mean_lat) * 111.32
    dy = (lat_b - lat_a) * 110.57
    return math.hypot(dx, dy)


def _distance_to_bounds_km(lon: float, lat: float, lon_min: float, lat_min: float,
                           lon_max: float, lat_max: float) -> float:
    """Distance from a catalog point to a patch's lon/lat bounding box, 0 inside it.

    Catalog coordinates are open-water points, not centroids, and a large lake's area
    centroid can sit farther from them than the match radius: Lake Superior's centroid is
    31 km from its catalog point, so a centroid-to-point test left the largest lake in the
    region named "Water body - 47.5N 87.2W". A point anywhere inside the patch's footprint
    is a match; a point outside it still has to be within the radius, so two nearby small
    lakes cannot swap names.
    """
    clamped_lon = min(max(lon, min(lon_min, lon_max)), max(lon_min, lon_max))
    clamped_lat = min(max(lat, min(lat_min, lat_max)), max(lat_min, lat_max))
    return _distance_km(lon, lat, clamped_lon, clamped_lat)
