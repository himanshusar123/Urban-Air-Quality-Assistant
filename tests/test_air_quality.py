import httpx
import pytest
import respx

from app.config import Settings
from app.services.air_quality import (
    AirQualityService,
    LocationNotFoundError,
    UpstreamError,
    parse_snapshot,
)
from tests.conftest import SAMPLE_AQ_PAYLOAD, SAMPLE_GEO_PAYLOAD

SETTINGS = Settings(gemini_api_key=None)


def test_parse_snapshot_normalises_payload() -> None:
    snap = parse_snapshot(SAMPLE_AQ_PAYLOAD, 28.61, 77.21, "Delhi")
    assert snap.aqi == 172
    assert snap.pollutants.pm2_5 == pytest.approx(96.3)
    assert snap.timezone == "Asia/Kolkata"
    # Hours before the observation time and null values are dropped.
    assert snap.forecast[0].time == "2026-11-18T08:00"
    assert all(point.aqi is not None for point in snap.forecast)
    assert len(snap.forecast) == 15


def test_parse_snapshot_requires_current_reading() -> None:
    with pytest.raises(UpstreamError):
        parse_snapshot({"current": {}}, 0, 0, "Nowhere")


@pytest.mark.asyncio
@respx.mock
async def test_geocode_and_cache() -> None:
    route = respx.get(SETTINGS.geocoding_url).mock(
        return_value=httpx.Response(200, json=SAMPLE_GEO_PAYLOAD)
    )
    async with httpx.AsyncClient() as client:
        service = AirQualityService(client, SETTINGS)
        first = await service.geocode("Delhi")
        second = await service.geocode("delhi")  # case-insensitive cache hit
    assert first == ("Delhi, India", 28.65, 77.23)  # duplicate "Delhi" removed
    assert second == first
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_geocode_unknown_city() -> None:
    respx.get(SETTINGS.geocoding_url).mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(LocationNotFoundError):
            await AirQualityService(client, SETTINGS).geocode("Atlantis")


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_uses_cache_for_nearby_coordinates() -> None:
    route = respx.get(SETTINGS.air_quality_url).mock(
        return_value=httpx.Response(200, json=SAMPLE_AQ_PAYLOAD)
    )
    async with httpx.AsyncClient() as client:
        service = AirQualityService(client, SETTINGS)
        a = await service.snapshot(28.61001, 77.21001, "Delhi")
        b = await service.snapshot(28.61004, 77.20999, "Delhi")
    assert a.aqi == b.aqi == 172
    assert route.call_count == 1
    sent = route.calls[0].request.url.params
    assert sent["timezone"] == "auto"
    assert "us_aqi" in sent["current"]


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, text="not json"), httpx.Response(200, json=[1])],
)
async def test_upstream_failures_raise_friendly_error(response: httpx.Response) -> None:
    respx.get(SETTINGS.air_quality_url).mock(return_value=response)
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError):
            await AirQualityService(client, SETTINGS).snapshot(1, 1, "X")


@pytest.mark.asyncio
@respx.mock
async def test_network_timeout_is_handled() -> None:
    respx.get(SETTINGS.air_quality_url).mock(side_effect=httpx.ConnectTimeout("boom"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError):
            await AirQualityService(client, SETTINGS).snapshot(1, 1, "X")
