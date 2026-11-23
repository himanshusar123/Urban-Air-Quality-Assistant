"""Maps domain errors to HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.air_quality import LocationNotFoundError, UpstreamError


def register_error_handlers(app: FastAPI) -> None:
    """Return 404 for unknown places and 502 when the data provider fails."""

    @app.exception_handler(LocationNotFoundError)
    async def _not_found(_: Request, exc: LocationNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(UpstreamError)
    async def _upstream(_: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)
