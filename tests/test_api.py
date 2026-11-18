from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.air_quality import UpstreamError
from app.services.explainer import Explainer
from tests.conftest import FakeAirService


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ai_enabled": False}


def test_index_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Skip to main content" in response.text


def test_advice_by_city(client: TestClient, fake_air: FakeAirService) -> None:
    response = client.post(
        "/api/advice", json={"city": "Delhi", "profile": "child", "activity": "kids_play"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["air"]["aqi"] == 165
    assert body["category"]["label"] == "Unhealthy"
    assert body["decision"]["verdict"] in {"limit", "avoid"}
    assert body["ai_generated"] is False
    assert body["explanation"]
    assert fake_air.geocode_calls == ["Delhi"]


def test_advice_by_coordinates_skips_geocoding(
    client: TestClient, fake_air: FakeAirService
) -> None:
    response = client.post("/api/advice", json={"latitude": 19.07, "longitude": 72.88})
    assert response.status_code == 200
    assert response.json()["air"]["location_name"].startswith("Your location")
    assert fake_air.geocode_calls == []


def test_unknown_city_returns_404(client: TestClient) -> None:
    response = client.post("/api/advice", json={"city": "Atlantis"})
    assert response.status_code == 404
    assert "Atlantis" in response.json()["detail"]


def test_validation_errors(client: TestClient) -> None:
    bad_payloads = [
        {},  # no location
        {"latitude": 10},  # partial coordinates
        {"latitude": 95, "longitude": 10},  # out of range
        {"city": "Delhi", "duration_minutes": 1000},
        {"city": "Delhi", "profile": "superhero"},
        {"city": "Delhi", "question": "x" * 301},
        {"city": "D"},
    ]
    for payload in bad_payloads:
        assert client.post("/api/advice", json=payload).status_code == 422, payload


def test_upstream_failure_returns_502(settings: Settings) -> None:
    class BrokenService(FakeAirService):
        async def snapshot(self, latitude, longitude, name):
            raise UpstreamError("Air-quality provider is unavailable.")

    app = create_app(settings=settings, air_service=BrokenService(), explainer=Explainer(None))
    with TestClient(app) as test_client:
        response = test_client.post("/api/advice", json={"city": "Delhi"})
    assert response.status_code == 502


def test_ai_explanation_is_returned_when_enabled(settings: Settings) -> None:
    async def fake_generate(system: str, prompt: str) -> str:
        return "Gemini says: take it easy."

    app = create_app(
        settings=settings, air_service=FakeAirService(), explainer=Explainer(fake_generate)
    )
    with TestClient(app) as test_client:
        body = test_client.post("/api/advice", json={"city": "Delhi"}).json()
        assert test_client.get("/api/health").json()["ai_enabled"] is True
    assert body["ai_generated"] is True
    assert body["explanation"] == "Gemini says: take it easy."
