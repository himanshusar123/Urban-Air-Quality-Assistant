"""AirWise — Urban Air Quality Assistant (FastAPI entry point)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.schemas import AdviceRequest, AdviceResponse
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware, SlidingWindowRateLimiter
from app.services.advisor import decide
from app.services.air_quality import AirQualityService, LocationNotFoundError, UpstreamError
from app.services.aqi import categorize
from app.services.explainer import Explainer, make_gemini_generate

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("airwise")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None,
    air_service: AirQualityService | None = None,
    explainer: Explainer | None = None,
) -> FastAPI:
    """Application factory. Services can be injected for testing."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client: httpx.AsyncClient | None = None
        if air_service is None:
            client = httpx.AsyncClient(
                headers={"User-Agent": "AirWise/1.0"},
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            app.state.air_service = AirQualityService(client, settings)
        else:
            app.state.air_service = air_service

        if explainer is None:
            generate = (
                make_gemini_generate(settings.gemini_api_key, settings.gemini_model)
                if settings.gemini_api_key
                else None
            )
            app.state.explainer = Explainer(generate, settings.cache_ttl_seconds)
        else:
            app.state.explainer = explainer
        logger.info("AirWise started (AI explanations: %s)", app.state.explainer.enabled)
        try:
            yield
        finally:
            if client is not None:
                await client.aclose()

    app = FastAPI(
        title="AirWise – Urban Air Quality Assistant",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # Middleware (last added runs first).
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    app.add_middleware(
        RateLimitMiddleware, limiter=SlidingWindowRateLimiter(settings.rate_limit_per_minute)
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(LocationNotFoundError)
    async def _not_found(_: Request, exc: LocationNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(UpstreamError)
    async def _upstream(_: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    def get_air_service(request: Request) -> AirQualityService:
        return request.app.state.air_service

    def get_explainer(request: Request) -> Explainer:
        return request.app.state.explainer

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        return {"status": "ok", "ai_enabled": request.app.state.explainer.enabled}

    @app.post("/api/advice", response_model=AdviceResponse)
    async def advice(
        body: AdviceRequest,
        air_service: AirQualityService = Depends(get_air_service),
        explainer: Explainer = Depends(get_explainer),
    ) -> AdviceResponse:
        """Get live air quality and a personalised recommendation."""
        if body.latitude is not None and body.longitude is not None:
            name = body.city or f"Your location ({body.latitude:.2f}, {body.longitude:.2f})"
            lat, lon = body.latitude, body.longitude
        else:
            # AdviceRequest validation guarantees a city when coordinates are absent.
            name, lat, lon = await air_service.geocode(body.city or "")

        air = await air_service.snapshot(lat, lon, name)
        category = categorize(air.aqi)
        decision = decide(air, body.profile, body.activity, body.duration_minutes)
        explanation, ai_generated = await explainer.explain(
            air,
            category,
            decision,
            body.profile,
            body.activity,
            body.duration_minutes,
            body.question,
            body.language,
        )
        return AdviceResponse(
            air=air,
            category=category,
            decision=decision,
            explanation=explanation,
            ai_generated=ai_generated,
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
