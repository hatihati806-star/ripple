"""Rule R4 -- water masking.

NDWI and MNDWI were both tested and both FAILED on turbid water. On western Lake Erie
NDWI (green/NIR) flagged 0.2% and MNDWI (green/SWIR) flagged 0.1% of 23,153 obvious water
pixels; turbid water raises NIR and SWIR, driving both indices negative over open water.
Neither is used for masking here.

Instead:

* SCL class 6 gives per-day water usability. It classified 99.2% of that Erie AOI
  correctly.
* A static JRC Global Surface Water occurrence mask supplies the long-term backbone, which
  permanently removes land false-positives and lets ambiguous class 7 through.
"""
from __future__ import annotations

import numpy as np

from .config import SCL_UNCLASSIFIED, SCL_UNUSABLE, SCL_WATER


def scl_usable(scl: np.ndarray) -> np.ndarray:
    """Pixels SCL considers observable at all (drops cloud, shadow, snow, nodata)."""
    out = np.ones(scl.shape, dtype=bool)
    for cls in SCL_UNUSABLE:
        out &= (scl != cls)
    return out


def scl_water(scl: np.ndarray) -> np.ndarray:
    """Strict SCL water classification."""
    return scl == SCL_WATER


def combine_water_mask(scl: np.ndarray,
                       jrc_water: np.ndarray | None = None) -> np.ndarray:
    """Final water mask.

    Without a JRC mask: strict SCL water only.

    With a JRC mask: strict SCL water confirmed by JRC, plus ``unclassified`` pixels that
    JRC confirms are water. Unusable classes (cloud, shadow, snow) are always excluded,
    so a cloud pixel can never become water merely because water lies beneath it.
    """
    usable = scl_usable(scl)
    water = scl_water(scl) & usable
    if jrc_water is None:
        return water

    water &= jrc_water
    ambiguous = (scl == SCL_UNCLASSIFIED) & jrc_water & usable
    return water | ambiguous
