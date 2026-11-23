"""Tests for application wiring: default services, docs flag, config parsing."""

from fastapi.testclient import TestClient

from app.config import Settings, _bool_env, _int_env
from app.main import create_app
from app.schemas import AdviceRequest, HourlyPoint, Language, Verdict
from app.services.air_quality import parse_snapshot
from tests.conftest import SAMPLE_AQ_PAYLOAD


def test_default_services_are_built_without_injection() -> None:
    app = create_app(settings=Settings(gemini_api_key=None, rate_limit_per_minute=100))
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok", "ai_enabled": False}


def test_gemini_is_enabled_when_key_is_configured() -> None:
    app = create_app(settings=Settings(gemini_api_key="test-key", rate_limit_per_minute=100))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ai_enabled"] is True


def test_docs_are_off_by_default_and_opt_in() -> None:
    with TestClient(create_app(settings=Settings(gemini_api_key=None))) as client:
        assert client.get("/api/docs").status_code == 404
    with TestClient(create_app(settings=Settings(gemini_api_key=None, enable_docs=True))) as client:
        assert client.get("/api/openapi.json").status_code == 200


def test_env_parsing_helpers(monkeypatch) -> None:
    monkeypatch.setenv("X_INT", "42")
    assert _int_env("X_INT", 1) == 42
    monkeypatch.setenv("X_INT", "abc")
    assert _int_env("X_INT", 1) == 1
    monkeypatch.setenv("X_INT", "-5")
    assert _int_env("X_INT", 1) == 1
    monkeypatch.setenv("X_FLAG", "Yes")
    assert _bool_env("X_FLAG") is True
    monkeypatch.delenv("X_FLAG")
    assert _bool_env("X_FLAG") is False


def test_forecast_points_carry_a_category() -> None:
    air = parse_snapshot(SAMPLE_AQ_PAYLOAD, 28.6, 77.2, "Delhi")
    assert all(p.category for p in air.forecast)
    assert air.forecast[0].category == "Unhealthy"


def test_request_context_and_verdict_enum() -> None:
    req = AdviceRequest(city="Delhi", language=Language.HINDI, question="  hi  ")
    assert req.context.language is Language.HINDI
    assert req.context.question == "hi"
    assert [v.value for v in Verdict] == ["go", "caution", "limit", "avoid"]
    assert HourlyPoint(time="2026-11-18T08:00", aqi=10).category is None
