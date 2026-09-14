"""Open-Meteo forecast client. No API key required."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .config import OPEN_METEO_FORECAST

HOURLY_VARS = (
    "precipitation",
    "precipitation_probability",
    "temperature_2m",
    "soil_moisture_0_to_1cm",
    "soil_moisture_1_to_3cm",
    "soil_moisture_3_to_9cm",
)

_TIMEOUT_S = 30


def fetch_forecast(lat: float, lon: float, days: int = 7,
                   past_days: int = 2) -> dict:
    """Hourly forecast for a point."""
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": ",".join(HOURLY_VARS),
        "forecast_days": str(int(days)),
        "past_days": str(int(past_days)),
        "timezone": "UTC",
    }
    url = f"{OPEN_METEO_FORECAST}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def daily_rainfall(forecast: dict) -> list[tuple[str, float]]:
    """Sum hourly precipitation into UTC-day totals -> [(YYYY-MM-DD, mm), ...]."""
    hours = forecast["hourly"]["time"]
    precip = forecast["hourly"]["precipitation"]
    totals: dict[str, float] = {}
    for stamp, mm in zip(hours, precip):
        day = stamp[:10]
        totals[day] = totals.get(day, 0.0) + float(mm or 0.0)
    return sorted(totals.items())


def mean_antecedent_moisture(forecast: dict) -> float:
    """Mean soil moisture (m3/m3) across the top three layers over the first 24 h."""
    layers = [forecast["hourly"].get(key) for key in (
        "soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm")]
    values: list[float] = []
    for layer in layers:
        if not layer:
            continue
        for value in layer[:24]:
            if value is not None:
                values.append(float(value))
    return sum(values) / len(values) if values else 0.25
