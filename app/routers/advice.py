"""HTTP endpoints: health check and personalised air-quality advice."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.schemas import AdviceRequest, AdviceResponse
from app.services.advisor import decide
from app.services.air_quality import AirQualityService
from app.services.aqi import categorize
from app.services.explainer import Explainer

router = APIRouter(prefix="/api")


def get_air_service(request: Request) -> AirQualityService:
    """Dependency: the shared air-quality service."""
    service: AirQualityService = request.app.state.air_service
    return service


def get_explainer(request: Request) -> Explainer:
    """Dependency: the shared explanation service."""
    explainer: Explainer = request.app.state.explainer
    return explainer


AirServiceDep = Annotated[AirQualityService, Depends(get_air_service)]
ExplainerDep = Annotated[Explainer, Depends(get_explainer)]


@router.get("/health")
async def health(explainer: ExplainerDep) -> dict[str, object]:
    """Liveness probe; also reports whether Gemini explanations are enabled."""
    return {"status": "ok", "ai_enabled": explainer.enabled}


async def _resolve_location(
    body: AdviceRequest, air_service: AirQualityService
) -> tuple[str, float, float]:
    """Use supplied coordinates, or geocode the city name."""
    if body.latitude is not None and body.longitude is not None:
        name = body.city or f"Your location ({body.latitude:.2f}, {body.longitude:.2f})"
        return name, body.latitude, body.longitude
    # AdviceRequest validation guarantees a city when coordinates are absent.
    return await air_service.geocode(body.city or "")


@router.post("/advice", response_model=AdviceResponse)
async def advice(
    body: AdviceRequest, air_service: AirServiceDep, explainer: ExplainerDep
) -> AdviceResponse:
    """Get live air quality and a personalised recommendation."""
    name, lat, lon = await _resolve_location(body, air_service)
    air = await air_service.snapshot(lat, lon, name)
    category = categorize(air.aqi)
    ctx = body.context
    decision = decide(air, ctx)
    explanation, ai_generated = await explainer.explain(air, category, decision, ctx)
    return AdviceResponse(
        air=air,
        category=category,
        decision=decision,
        explanation=explanation,
        ai_generated=ai_generated,
    )
