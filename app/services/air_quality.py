"""Live data access: Open-Meteo geocoding and air-quality APIs (no key needed)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.schemas import AirSnapshot, HourlyPoint, Pollutants
from app.services.aqi import categorize

logger = logging.getLogger(__name__)

POLLUTANT_FIELDS = (
    "pm2_5",
    "pm10",
    "ozone",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "carbon_monoxide",
)
FORECAST_HOURS = 24


class LocationNotFoundError(Exception):
    """The requested city could not be geocoded."""


class UpstreamError(Exception):
    """An external data provider failed or returned malformed data."""


class AirQualityService:
    """Fetches and caches live air quality for a place, via Open-Meteo."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: TTLCache[Any] = TTLCache(
            settings.cache_ttl_seconds, settings.cache_max_entries
        )

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.get(
                url, params=params, timeout=self._settings.http_timeout_seconds
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Upstream request failed: %s", type(exc).__name__)
            msg = "Air-quality provider is unavailable. Please retry shortly."
            raise UpstreamError(msg) from exc
        if not isinstance(data, dict):
            raise UpstreamError("Unexpected response from provider.")
        return data

    async def geocode(self, city: str) -> tuple[str, float, float]:
        """Resolve a city name to (display name, latitude, longitude)."""
        key = f"geo:{city.casefold()}"
        cached = self._cache.get(key)
        if cached is not None:
            return cast("tuple[str, float, float]", cached)

        data = await self._get_json(
            self._settings.geocoding_url,
            {"name": city, "count": 1, "language": "en", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise LocationNotFoundError(f"Could not find a place called '{city}'.")
        top = results[0]
        parts = [top.get("name"), top.get("admin1"), top.get("country")]
        name = ", ".join(dict.fromkeys(p for p in parts if p))  # dedupe, keep order
        result = (name, float(top["latitude"]), float(top["longitude"]))
        self._cache.set(key, result)
        return result

    async def snapshot(self, latitude: float, longitude: float, name: str) -> AirSnapshot:
        """Fetch current AQI, pollutant levels and a 24-hour AQI forecast."""
        lat, lon = round(latitude, 3), round(longitude, 3)  # ~100 m; improves cache hits
        key = f"aq:{lat}:{lon}"
        cached = self._cache.get(key)
        if cached is None:
            cached = await self._get_json(
                self._settings.air_quality_url,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "current": ",".join(("us_aqi", *POLLUTANT_FIELDS)),
                    "hourly": "us_aqi",
                    "forecast_days": 2,
                    "timezone": "auto",
                },
            )
            self._cache.set(key, cached)
        return parse_snapshot(cached, lat, lon, name)


def _parse_forecast(hourly: dict[str, Any], observed_at: str) -> list[HourlyPoint]:
    """Turn Open-Meteo's parallel ``time``/``us_aqi`` arrays into upcoming hourly points."""
    times = hourly.get("time") or []
    values = hourly.get("us_aqi") or []
    points: list[HourlyPoint] = []
    for time, value in zip(times, values, strict=False):
        if value is None or time < observed_at:
            continue
        aqi = round(value)
        points.append(HourlyPoint(time=time, aqi=aqi, category=categorize(aqi).label))
    return points[:FORECAST_HOURS]


def parse_snapshot(data: dict[str, Any], lat: float, lon: float, name: str) -> AirSnapshot:
    """Convert a raw Open-Meteo payload into an :class:`AirSnapshot`."""
    current = data.get("current") or {}
    aqi = current.get("us_aqi")
    observed_at = current.get("time")
    if aqi is None or observed_at is None:
        raise UpstreamError("No current air-quality reading is available for this location.")

    return AirSnapshot(
        location_name=name,
        latitude=lat,
        longitude=lon,
        timezone=str(data.get("timezone", "UTC")),
        observed_at=observed_at,
        aqi=round(aqi),
        pollutants=Pollutants(**{f: current.get(f) for f in POLLUTANT_FIELDS}),
        forecast=_parse_forecast(data.get("hourly") or {}, observed_at),
    )
