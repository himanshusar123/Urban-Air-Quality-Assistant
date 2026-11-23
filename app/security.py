"""Security middleware: HTTP hardening headers and per-client rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add hardening headers to every response and disable caching of API responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` requests per ``window`` seconds per key.

    State is per process. Idle keys are swept once ``max_keys`` is exceeded, so
    memory stays bounded however many distinct clients connect.
    """

    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._max_keys = max_keys
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def _sweep(self, now: float) -> None:
        """Drop keys whose most recent hit is outside the window."""
        stale = [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]
        for key in stale:
            del self._hits[key]

    def allow(self, key: str) -> bool:
        """Record a request for ``key`` and say whether it is within the limit."""
        now = self._clock()
        if len(self._hits) > self._max_keys:
            self._sweep(now)
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit POST API calls (the ones that hit upstream APIs and Gemini)."""

    def __init__(self, app: ASGIApp, limiter: SlidingWindowRateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST" and request.url.path.startswith("/api/"):
            client = request.client.host if request.client else "unknown"
            if not self.limiter.allow(client):
                return JSONResponse(
                    {"detail": "Too many requests. Please wait a minute and try again."},
                    status_code=429,
                    headers={"Retry-After": str(int(self.limiter.window))},
                )
        return await call_next(request)
