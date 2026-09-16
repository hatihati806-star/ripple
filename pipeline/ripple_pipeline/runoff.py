"""SCS Curve Number rainfall-runoff model.

A standard published engineering method (USDA NRCS), not a heuristic:

    S      = 25400 / CN - 254
    Ia     = 0.2 S
    runoff = (P - Ia)^2 / (P - Ia + S)   for P > Ia, else 0

This estimates *relative* sediment and nutrient loading risk, for ranking. It is never
presented as a concentration, and no calibrated yield is implied.
"""
from __future__ import annotations

# Base curve numbers for hydrologic soil group B (moderate infiltration).
_BASE_CN_B = {
    "row_crop": 78.0,
    "bare": 86.0,
    "pasture": 61.0,
    "forest": 55.0,
    "wetland": 70.0,
    "urban": 88.0,
    "water": 100.0,
}

# Relative sediment-delivery multipliers: ranking weights, not calibrated yields.
EROSION_FACTORS = {
    "row_crop": 1.0,
    "bare": 1.4,
    "pasture": 0.5,
    "forest": 0.15,
    "wetland": 0.1,
    "urban": 1.2,
    "water": 0.0,
}

# Adjustment from soil group B to the other hydrologic soil groups.
_SOIL_DELTA = {"A": -8.0, "B": 0.0, "C": 6.0, "D": 12.0}

# Antecedent moisture condition breakpoints (m3/m3) and their CN adjustments.
_AMC_DRY = 0.15
_AMC_WET = 0.35
_AMC_DRY_DELTA = -6.0
_AMC_WET_DELTA = 8.0

# The same CN adjustments driven by 5-day antecedent rainfall instead of soil moisture,
# at the standard USDA NRCS dormant-season AMC I/II (13 mm) and II/III (28 mm) breaks.
# Used by the gridded forecast path, where a soil-moisture field is not fetched.
AMC_DRY_RAIN_MM = 13.0
AMC_WET_RAIN_MM = 28.0

_CN_MIN = 30.0
_CN_MAX = 100.0


def curve_number(land_cover: str, soil_group: str,
                 antecedent_moisture: float) -> float:
    """Effective SCS curve number, clamped to [30, 100].

    Raises ``KeyError`` for an unknown land cover or soil group.
    """
    cn = _BASE_CN_B[land_cover]
    if soil_group not in _SOIL_DELTA:
        raise KeyError(f"unknown soil group: {soil_group}")
    cn += _SOIL_DELTA[soil_group]

    if antecedent_moisture <= _AMC_DRY:
        cn += _AMC_DRY_DELTA
    elif antecedent_moisture >= _AMC_WET:
        cn += _AMC_WET_DELTA

    return float(min(_CN_MAX, max(_CN_MIN, cn)))


def cn_from_antecedent_rainfall(land_cover: str, soil_group: str,
                                antecedent_rain_mm):
    """Effective CN from 5-day antecedent rainfall. Accepts scalars or NumPy arrays.

    The AMC class and CN deltas are the same ones ``curve_number`` uses, so the point
    forecast (soil moisture) and the gridded forecast (antecedent rainfall) cannot drift.
    """
    import numpy as np

    if soil_group not in _SOIL_DELTA:
        raise KeyError(f"unknown soil group: {soil_group}")
    base = _BASE_CN_B[land_cover] + _SOIL_DELTA[soil_group]
    rain = np.asarray(antecedent_rain_mm, dtype="float64")
    delta = np.where(rain <= AMC_DRY_RAIN_MM, _AMC_DRY_DELTA,
                     np.where(rain >= AMC_WET_RAIN_MM, _AMC_WET_DELTA, 0.0))
    return np.clip(base + delta, _CN_MIN, _CN_MAX)


def curve_number_from_rainfall(land_cover: str, soil_group: str,
                               antecedent_rain_mm: float) -> float:
    """Scalar form of :func:`cn_from_antecedent_rainfall`."""
    return float(cn_from_antecedent_rainfall(land_cover, soil_group, antecedent_rain_mm))


def runoff_from_cn(rain_mm, cn):
    """Vectorized SCS direct runoff depth. NaN rainfall propagates as NaN (unknown in,
    unknown out); non-positive rainfall yields zero."""
    import numpy as np

    p = np.asarray(rain_mm, dtype="float64")
    storage = (25400.0 / np.asarray(cn, dtype="float64")) - 254.0
    ia = 0.2 * storage
    denom = p - ia + storage
    with np.errstate(invalid="ignore", divide="ignore"):
        runoff = np.where(p > ia, ((p - ia) ** 2) / denom, 0.0)
    return np.clip(runoff, 0.0, np.maximum(p, 0.0))


def direct_runoff_mm(rain_mm: float, cn: float) -> float:
    """SCS direct runoff depth in mm for a rainfall depth in mm."""
    if rain_mm <= 0.0:
        return 0.0
    storage = (25400.0 / cn) - 254.0
    if storage <= 0.0:
        return float(rain_mm)
    initial_abstraction = 0.2 * storage
    if rain_mm <= initial_abstraction:
        return 0.0
    return float(((rain_mm - initial_abstraction) ** 2)
                 / (rain_mm - initial_abstraction + storage))


def load_risk(runoff_mm: float, land_cover: str) -> float:
    """Relative loading risk = runoff depth x erosion multiplier."""
    return float(runoff_mm * EROSION_FACTORS[land_cover])
