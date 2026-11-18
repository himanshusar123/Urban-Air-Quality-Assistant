"""Shared fixtures: sample Open-Meteo payloads and a test client with fakes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import AirSnapshot, HourlyPoint, Pollutants
from app.services.explainer import Explainer


def make_forecast(start_hour: int, values: list[int], day: str = "2026-11-18") -> list[HourlyPoint]:
    return [HourlyPoint(time=f"{day}T{start_hour + i:02d}:00", aqi=v) for i, v in enumerate(values)]


def make_air(
    aqi: int = 80, forecast: list[HourlyPoint] | None = None, **pollutants: float
) -> AirSnapshot:
    return AirSnapshot(
        location_name="Delhi, India",
        latitude=28.61,
        longitude=77.21,
        timezone="Asia/Kolkata",
        observed_at="2026-11-18T08:00",
        aqi=aqi,
        pollutants=Pollutants(**pollutants),
        forecast=forecast if forecast is not None else make_forecast(8, [aqi] * 12),
    )


SAMPLE_AQ_PAYLOAD = {
    "timezone": "Asia/Kolkata",
    "current": {
        "time": "2026-11-18T08:00",
        "us_aqi": 172.4,
        "pm2_5": 96.3,
        "pm10": 160.0,
        "ozone": 20.0,
        "nitrogen_dioxide": 55.0,
        "sulphur_dioxide": 9.0,
        "carbon_monoxide": 900.0,
    },
    "hourly": {
        "time": [f"2026-11-18T{h:02d}:00" for h in range(6, 24)],
        "us_aqi": [
            180,
            178,
            172,
            165,
            150,
            130,
            110,
            95,
            90,
            92,
            100,
            120,
            140,
            160,
            170,
            175,
            178,
            None,
        ],
    },
}

SAMPLE_GEO_PAYLOAD = {
    "results": [
        {
            "name": "Delhi",
            "admin1": "Delhi",
            "country": "India",
            "latitude": 28.65,
            "longitude": 77.23,
        }
    ]
}


class FakeAirService:
    """Stands in for AirQualityService in API tests."""

    def __init__(self, air: AirSnapshot | None = None) -> None:
        self.air = air or make_air(aqi=165, pm2_5=90.0)
        self.geocode_calls: list[str] = []

    async def geocode(self, city: str) -> tuple[str, float, float]:
        from app.services.air_quality import LocationNotFoundError

        self.geocode_calls.append(city)
        if city.lower() == "atlantis":
            raise LocationNotFoundError("Could not find a place called 'Atlantis'.")
        return "Delhi, India", 28.61, 77.21

    async def snapshot(self, latitude: float, longitude: float, name: str) -> AirSnapshot:
        return self.air.model_copy(update={"location_name": name})


@pytest.fixture
def settings() -> Settings:
    return Settings(gemini_api_key=None, rate_limit_per_minute=1000)


@pytest.fixture
def fake_air() -> FakeAirService:
    return FakeAirService()


@pytest.fixture
def client(settings: Settings, fake_air: FakeAirService) -> Iterator[TestClient]:
    app = create_app(settings=settings, air_service=fake_air, explainer=Explainer(None))
    with TestClient(app) as test_client:
        yield test_client
