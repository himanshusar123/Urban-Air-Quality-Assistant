"""AirWise — Urban Air Quality Assistant (FastAPI entry point).

Run with ``uvicorn --factory app.main:create_app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.errors import register_error_handlers
from app.routers import advice
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware, SlidingWindowRateLimiter
from app.services.air_quality import AirQualityService
from app.services.explainer import Explainer, make_gemini_generate

logger = logging.getLogger("airwise")

STATIC_DIR = Path(__file__).parent / "static"
APP_VERSION = "1.0.0"
USER_AGENT = f"AirWise/{APP_VERSION}"


def _build_explainer(settings: Settings) -> Explainer:
    """Use Gemini when a key is configured, otherwise the template fallback."""
    generate = (
        make_gemini_generate(settings.gemini_api_key, settings.gemini_model)
        if settings.gemini_api_key
        else None
    )
    return Explainer(generate, settings.cache_ttl_seconds)


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    """Register middleware. The last one added runs first."""
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


def create_app(
    settings: Settings | None = None,
    air_service: AirQualityService | None = None,
    explainer: Explainer | None = None,
) -> FastAPI:
    """Application factory. Services can be injected for testing."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        client = (
            None
            if air_service is not None
            else httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        )
        if air_service is not None:
            app.state.air_service = air_service
        elif client is not None:
            app.state.air_service = AirQualityService(client, settings)
        app.state.explainer = explainer or _build_explainer(settings)
        logger.info("AirWise started (AI explanations: %s)", app.state.explainer.enabled)
        try:
            yield
        finally:
            if client is not None:
                await client.aclose()

    app = FastAPI(
        title="AirWise – Urban Air Quality Assistant",
        version=APP_VERSION,
        lifespan=lifespan,
        # Swagger UI loads assets from a CDN, which our strict CSP blocks; opt in for local use.
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )
    _add_middleware(app, settings)
    register_error_handlers(app)
    app.include_router(advice.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
