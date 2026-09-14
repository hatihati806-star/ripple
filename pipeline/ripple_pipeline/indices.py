"""Optical water-quality indices and the composite risk score.

    NDCI = (B05 - B04) / (B05 + B04)   chlorophyll-a     Mishra & Mishra 2012
    NDTI = (B04 - B03) / (B04 + B03)   turbidity         Lacaux et al. 2007

Measured calibration anchors (2026 scenes, 250 m grid):

    Lake Superior offshore   NDCI +0.007   NDTI +0.031   clear
    W. Lake Erie basin       NDCI +0.010   NDTI +0.174   turbid

NDTI is strongly regional: 0.174 is normal for western Erie and would be an anomaly on
Superior. It is therefore normalized against a per-water-body baseline rather than a
global threshold, and the UI labels the thresholds as provisional.

No cyanobacteria or toxicity claim is made anywhere in this module. Discriminating
cyanobacteria from green algae requires the phycocyanin absorption feature near 620 nm,
which Sentinel-2 does not sample (nearest bands are 665 nm and 705 nm), so v1 reports
bloom intensity only.
"""
from __future__ import annotations

import numpy as np

from .config import BLOOM_NDCI_THRESHOLD, NDCI_BANDS

_EPS = 1e-6


def _safe_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), NaN where the denominator vanishes."""
    denom = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    ok = np.isfinite(denom) & (np.abs(denom) > _EPS) & np.isfinite(a) & np.isfinite(b)
    out[ok] = ((a[ok] - b[ok]) / denom[ok]).astype("float32")
    return out


def ndci(red: np.ndarray, rededge1: np.ndarray) -> np.ndarray:
    """Normalized Difference Chlorophyll Index -- algal bloom intensity."""
    return _safe_ratio(rededge1, red)


def ndti(red: np.ndarray, green: np.ndarray) -> np.ndarray:
    """Normalized Difference Turbidity Index -- suspended sediment proxy."""
    return _safe_ratio(red, green)


def normalize_ndci(v: np.ndarray) -> np.ndarray:
    """Map NDCI onto [0, 1] using the literature bloom bands."""
    lo, _, hi = NDCI_BANDS
    out = (np.nan_to_num(v, nan=lo) - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0).astype("float32")


def normalize_ndti(v: np.ndarray, p05: float, p95: float) -> np.ndarray:
    """Map NDTI onto [0, 1] against this water body's own 5th/95th percentile range."""
    span = float(p95) - float(p05)
    if abs(span) < _EPS:
        return np.zeros(v.shape, dtype="float32")
    out = (np.nan_to_num(v, nan=p05) - float(p05)) / span
    return np.clip(out, 0.0, 1.0).astype("float32")


def normalize_relative(v: np.ndarray, p05: float, p95: float) -> np.ndarray:
    """Map any index onto [0, 1] against an observed p05/p95 range.

    Both NDCI and NDTI are normalized this way so the colour ramp has one coherent
    meaning: position within the range actually observed in this region. An absolute
    threshold applied to NDCI (0.1) would otherwise saturate the ramp whenever a
    widespread bloom is present, which is exactly when the map matters most.
    """
    return normalize_ndti(v, p05, p95)


def percentile_range(v: np.ndarray, lo: float = 5.0,
                     hi: float = 95.0) -> tuple[float, float]:
    """Observed p05/p95 of finite values, with a safe fallback for empty input."""
    finite = v[np.isfinite(v)]
    if finite.size == 0:
        return 0.0, 1.0
    p05, p95 = np.percentile(finite, [lo, hi])
    if abs(float(p95) - float(p05)) < _EPS:
        return float(p05), float(p05) + 1.0
    return float(p05), float(p95)


def possible_bloom_mask(ndci_values: np.ndarray,
                        threshold: float = BLOOM_NDCI_THRESHOLD) -> np.ndarray:
    """Absolute flag for probable algal bloom, independent of the relative colour ramp.

    This uses the published NDCI threshold and is reported separately from the risk
    colour. It indicates bloom *intensity* only -- never cyanobacteria and never toxicity.
    """
    return np.isfinite(ndci_values) & (ndci_values > threshold)


def risk_score(ndci_v: np.ndarray, ndti_v: np.ndarray) -> np.ndarray:
    """Composite 0..1 risk: the worse of the two normalized signals.

    ``max`` rather than a product so a single strong signal still surfaces -- an obvious
    bloom must not be diluted by otherwise clear-water turbidity.
    """
    return np.clip(np.fmax(ndci_v, ndti_v), 0.0, 1.0).astype("float32")
