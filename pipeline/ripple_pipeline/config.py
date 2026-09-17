"""Ripple pipeline configuration.

All values here are spec-locked; see
`docs/superpowers/specs/2026-09-14-ripple-water-quality-forecast-design.md`.
"""
from __future__ import annotations

# Continental region: the contiguous United States, Canada south of 54N, and Mexico.
# (lon_min, lat_min, lon_max, lat_max)
#
# One 4096-pixel overlay cannot span two continents at a usable resolution -- NA + SA is
# ~136 degrees of longitude, which lands at ~3.7 km/px where a 10 km2 lake is half a pixel.
# The honest fix for the Arctic, Alaska and South America is a tiled pyramid, not a bigger
# image; this region is the largest area that stays under 2 km/px in a single overlay.
REGION_NAME = "United States, southern Canada & Mexico"
REGION_BBOX: tuple[float, float, float, float] = (-128.0, 18.0, -60.0, 54.0)

# Analysis grid.
TARGET_RES_M: float = 250.0

# Water-body discovery. At the delivered resolution (~1.85 km) a 20 km2 lake is ~6 pixels,
# which is the smallest thing that can be sampled without the result being mostly shoreline.
BODY_MIN_AREA_KM2: float = 20.0
BODY_MAX_COUNT: int = 250
BODY_NAME_MATCH_KM: float = 25.0

# A grid cell counts as water when at least this fraction of its sub-samples are SCL water.
# The SCL band is categorical, so a single nearest-sampled 20 m pixel per 1.85 km cell is a
# coin flip on a shoreline and misses narrow reservoirs entirely; majority voting over
# sub-samples is what makes Lake Oahe or Kentucky Lake register at all.
MASK_WATER_FRACTION: float = 0.25
MASK_SUBSAMPLE: int = 2

# Reflectance transform. Rule R1: the STAC-declared offset (-0.1) is ALREADY baked into
# the stored DN for these items, so no offset is applied here.
REFLECTANCE_SCALE: float = 1e-4

# Nodata sentinel in the source COGs. Must be masked on RAW DN (rule R3).
NODATA_DN: int = 0

# Sentinel-2 scene classification layer classes.
SCL_NO_DATA = 0
SCL_SATURATED = 1
SCL_DARK_AREA = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_NOT_VEGETATED = 5
SCL_WATER = 6
SCL_UNCLASSIFIED = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_CIRRUS = 10
SCL_SNOW = 11

SCL_UNUSABLE = frozenset({
    SCL_NO_DATA, SCL_SATURATED, SCL_CLOUD_SHADOW,
    SCL_CLOUD_MEDIUM, SCL_CLOUD_HIGH, SCL_CIRRUS, SCL_SNOW,
})

# Physical sanity bounds asserted per scene (rule R1).
# A misapplied offset drives water reflectance to roughly -0.09; this floor tolerates
# legitimately dark water from a source that applies the baseline-04 offset correctly.
MIN_VALID_REFLECTANCE: float = -0.001

# Thresholds. Provisional and regionally variable; the UI labels them as such.
NDCI_BANDS: tuple[float, float, float] = (0.0, 0.1, 0.3)
NDTI_BANDS: tuple[float, float] = (0.05, 0.15)

# Absolute NDCI above which an algal bloom is probable (published threshold). Reported
# separately from the relative risk colour, and never presented as cyanobacteria.
BLOOM_NDCI_THRESHOLD: float = 0.1

# GloFAS snapping guard.
MIN_MEANINGFUL_DISCHARGE: float = 1.0  # m3/s

# Endpoints. All key-free.
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_FLOOD = "https://flood-api.open-meteo.com/v1/flood"
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"
USGS_NWIS_IV = "https://waterservices.usgs.gov/nwis/iv/"
USGS_NWIS_SITE = "https://waterservices.usgs.gov/nwis/site/"
