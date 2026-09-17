"""Rule R1 -- reflectance scaling.

Earth Search advertises ``scale: 0.0001, offset: -0.1`` for Sentinel-2 L2A items, but the
stored DN in these COGs already has that offset applied. Verified on baseline 05.12 items:
the GeoTIFFs declare ``scales=(1.0,) offsets=(0.0,)`` and carry no scale/offset tags.

Applying the advertised offset makes clear-water NIR reflectance negative over Lake
Superior (-0.0941), which is physically impossible. ``DN * 1e-4`` instead yields
blue 0.0202 -> green 0.0128 -> red 0.0071 -> NIR 0.0053, the correct clear-water spectrum.

We therefore derive the transform empirically and assert physical sanity per scene. A
scene that fails is halted, never warned past -- a wrong scale produces plausible-looking
indices with thresholds still applied, so the failure would otherwise be silent.

What the assertion checks, and what it deliberately does NOT
------------------------------------------------------------
The decisive signature of the misapplied offset is **negativity**: every water pixel's
bands land near -0.09. Reflectance cannot be negative, so that check alone catches it.

An earlier version of this module also required "clear-water NIR < 0.02". That was wrong:
it conflated a *broken scale* with a *turbid lake*. Lake Winnipeg -- one of the most
turbid, algae-prone lakes in North America -- legitimately shows elevated NIR, and the
check halted it. Physics is now checked with sign and ordering, not with an assumption
that clear water must be present in the scene.

**Second correction (2026-09-16, going continental).** The rule also required blue >= red
over water pixels. Expanding from the Upper Midwest to the whole continent brought in Great
Salt Lake, whose water is hypersaline and sediment-loaded: median blue 0.164 vs red 0.203.
That is a real mineral-water spectrum, not a broken scale, and it halted the entire
latest frame. The ordering test has therefore been demoted to a *reported statistic*
(``enforce_ordering=False`` by default), because it is a heuristic about one water type
rather than a bug signature. The bug it was meant to catch -- the double-applied offset --
drives **every band** to roughly -0.09 and is caught by the negativity floor, which remains
a hard failure. Two false positives from the same heuristic is the argument for keeping it
advisory.
"""
from __future__ import annotations

import numpy as np

from .config import MIN_VALID_REFLECTANCE, REFLECTANCE_SCALE

# A scene with no real signal is a failed read, not a measurement.
MIN_P99_BLUE_REFLECTANCE = 0.01


class ScalingError(RuntimeError):
    """Raised when a scene's derived reflectance fails the physical sanity check."""


def to_reflectance(dn: np.ndarray) -> np.ndarray:
    """Convert raw uint16 digital numbers to surface reflectance.

    No offset is applied -- see the module docstring.
    """
    if dn.dtype == np.float32:
        raise TypeError("to_reflectance expects raw integer DN, not float")
    return dn.astype(np.float32) * REFLECTANCE_SCALE


def validate_scaling(refl: dict[str, np.ndarray], valid: np.ndarray,
                     label: str = "", water: np.ndarray | None = None,
                     enforce_ordering: bool = False) -> dict:
    """Assert derived reflectance is physically sane, or raise ``ScalingError``.

    Hard checks, over ``valid`` pixels -- these are *bug* signatures, not water physics:

    1. all values finite;
    2. no significantly negative reflectance -- the signature of a double-applied offset;
    3. the scene carries real signal rather than being a degenerate read.

    Reported, over ``water`` pixels if supplied (otherwise over ``valid``):

    4. whether blue is darker than red. This is a *heuristic about clear water*, and it is
       returned rather than enforced by default, because it is wrong for whole classes of
       legitimate water. See the second correction in the module docstring.

    Returns the measured statistics so a caller can log what the check saw.
    """
    if not valid.any():
        raise ScalingError(f"{label}: no valid pixels to validate")

    for name, arr in refl.items():
        if not np.isfinite(arr[valid]).all():
            raise ScalingError(f"{label}: {name} contains non-finite values")

    worst = min(float(arr[valid].min()) for arr in refl.values())
    if worst < MIN_VALID_REFLECTANCE:
        raise ScalingError(
            f"{label}: negative reflectance ({worst:.4f}) -- the STAC offset was "
            f"applied but is already baked into the stored DN")

    if "blue" in refl:
        p99 = float(np.percentile(refl["blue"][valid], 99))
        if p99 < MIN_P99_BLUE_REFLECTANCE:
            raise ScalingError(
                f"{label}: blue p99 {p99:.4f} is implausibly low; the read is degenerate")

    surface = valid if water is None else water
    stats: dict = {"ordering_checked": bool(surface.any())}
    if "blue" in refl and "red" in refl and surface.any():
        stats["blue_median_over_surface"] = round(float(np.median(refl["blue"][surface])), 4)
        stats["red_median_over_surface"] = round(float(np.median(refl["red"][surface])), 4)
        stats["blue_dominant"] = bool(
            stats["blue_median_over_surface"] >= stats["red_median_over_surface"])
        if enforce_ordering and not stats["blue_dominant"]:
            scope = "all pixels" if water is None else "water pixels"
            raise ScalingError(
                f"{label}: blue ({stats['blue_median_over_surface']:.4f}) < "
                f"red ({stats['red_median_over_surface']:.4f}) over {scope}; "
                f"spectra are inverted")
    return stats
