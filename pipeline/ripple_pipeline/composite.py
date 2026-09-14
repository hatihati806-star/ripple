"""Rule R5 -- temporal best-pixel compositing.

A single satellite pass leaves cloud and swath holes that read as a data outage on a map.
Compositing over a rolling window (5 and 15 days) picks, per pixel, the most recent valid
observation, producing a gap-free radar-like layer. This is standard operational practice
and is what makes the layer look continuous rather than speckled.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Observation:
    """One scene's contribution over the AOI grid."""

    values: np.ndarray   # float32, NaN where invalid
    valid: np.ndarray    # bool
    age_days: float      # age of this scene relative to "now"


def best_pixel(observations: list[Observation]
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-pixel most-recent valid observation.

    Returns ``(value, age_days, source_index)``. Pixels never covered are NaN with age
    ``0.0`` and ``source_index`` ``-1``.

    Observations may be supplied in any order; recency is determined by ``age_days``, not
    list position.
    """
    if not observations:
        empty = np.zeros((0, 0), dtype="float32")
        return empty, empty.copy(), np.zeros((0, 0), dtype="int32")

    shape = observations[0].values.shape
    value = np.full(shape, np.nan, dtype="float32")
    age = np.zeros(shape, dtype="float32")
    src = np.full(shape, -1, dtype="int32")
    best_age = np.full(shape, np.inf, dtype="float32")

    for index, obs in enumerate(observations):
        if obs.values.shape != shape:
            raise ValueError(
                f"observation {index} shape {obs.values.shape} != {shape}")
        candidate = obs.valid & np.isfinite(obs.values) & (obs.age_days < best_age)
        value[candidate] = obs.values[candidate]
        age[candidate] = obs.age_days
        src[candidate] = index
        best_age[candidate] = obs.age_days

    return value, age, src
