from fastapi.testclient import TestClient

from app.cache import TTLCache
from app.config import Settings
from app.main import create_app
from app.schemas import AdviceRequest
from app.security import SECURITY_HEADERS, SlidingWindowRateLimiter
from app.services.explainer import Explainer
from tests.conftest import FakeAirService


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_security_headers_present(client: TestClient) -> None:
    for path in ("/", "/api/health"):
        response = client.get(path)
        for header in SECURITY_HEADERS:
            assert header in response.headers, (path, header)
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in client.get("/").headers["Content-Security-Policy"]


def test_rate_limit_returns_429() -> None:
    app = create_app(
        settings=Settings(gemini_api_key=None, rate_limit_per_minute=2),
        air_service=FakeAirService(),
        explainer=Explainer(None),
    )
    with TestClient(app) as test_client:
        codes = [
            test_client.post("/api/advice", json={"city": "Delhi"}).status_code for _ in range(3)
        ]
        assert test_client.get("/api/health").status_code == 200  # GETs not limited
    assert codes == [200, 200, 429]


def test_sliding_window_recovers_after_window() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(limit=1, window=60, clock=clock)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")  # keys are independent
    clock.now = 60
    assert limiter.allow("a")


def test_ttl_cache_expiry_and_eviction() -> None:
    clock = FakeClock()
    cache: TTLCache[int] = TTLCache(ttl_seconds=10, max_entries=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # "a" becomes most recently used
    cache.set("c", 3)  # evicts "b"
    assert cache.get("b") is None
    assert len(cache) == 2
    clock.now = 10
    assert cache.get("a") is None


def test_input_sanitisation_strips_control_characters() -> None:
    request = AdviceRequest(city="  Delhi\x00\x07 ", question="hi\x1b[31m there")
    assert request.city == "Delhi"
    assert request.question == "hi[31m there"


def test_whitespace_only_question_becomes_none() -> None:
    assert AdviceRequest(city="Pune", question="   ").question is None


def test_api_key_never_leaks_in_health(monkeypatch) -> None:
    app = create_app(
        settings=Settings(gemini_api_key="secret-key-123"),
        air_service=FakeAirService(),
        explainer=Explainer(None),
    )
    with TestClient(app) as test_client:
        assert "secret-key-123" not in test_client.get("/api/health").text
