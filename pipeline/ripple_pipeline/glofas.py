"""GloFAS river discharge via Open-Meteo's flood API. No API key required.

The snapping trap
-----------------
The API snaps *every* coordinate to the nearest modelled river cell and always returns a
number: a point in empty Saskatchewan returned 0.02 m3/s, and a farmland point in Kansas
returned 0.04. Callers must therefore (a) only query points known to sit on river
geometry, and (b) reject responses below ``MIN_MEANINGFUL_DISCHARGE``.

Verified reference points: Memphis on the Mississippi (35.15, -90.05) reads ~16 m3/s;
empty Saskatchewan (50.5, -105.5) reads 0.02-0.08 m3/s.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import MIN_MEANINGFUL_DISCHARGE, OPEN_METEO_FLOOD

DAILY_VARS = (
    "river_discharge",
    "river_discharge_p25",
    "river_discharge_median",
    "river_discharge_p75",
)

_TIMEOUT_S = 30


@dataclass
class DischargeForecast:
    """Daily discharge ensemble values, all in m3/s."""

    dates: list[str]
    p25: list[float]
    median: list[float]
    p75: list[float]
    mean: list[float]


def fetch_discharge(lat: float, lon: float, forecast_days: int = 30,
                    past_days: int = 7) -> DischargeForecast:
    """Daily GloFAS discharge ensemble at the river cell nearest (lat, lon)."""
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": ",".join(DAILY_VARS),
        "forecast_days": str(int(forecast_days)),
        "past_days": str(int(past_days)),
    }
    url = f"{OPEN_METEO_FLOOD}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
        payload = json.loads(resp.read())["daily"]

    def series(primary: str, fallback: str) -> list[float]:
        raw = payload.get(primary)
        if raw is None:
            raw = payload.get(fallback) or []
        return [float(value) if value is not None else 0.0 for value in raw]

    return DischargeForecast(
        dates=list(payload["time"]),
        p25=series("river_discharge_p25", "river_discharge"),
        median=series("river_discharge_median", "river_discharge"),
        p75=series("river_discharge_p75", "river_discharge"),
        mean=series("river_discharge", "river_discharge_mean"),
    )


def has_meaningful_river(fc: DischargeForecast,
                         floor: float = MIN_MEANINGFUL_DISCHARGE) -> bool:
    """False when the response is a snapped dry cell rather than a real river."""
    if not fc.median:
        return False
    return max(fc.median) >= floor
