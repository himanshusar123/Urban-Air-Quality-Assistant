"""Application settings loaded from environment variables.

No secrets are ever hard-coded: the Gemini API key is read from the
environment (or injected by Cloud Run / Secret Manager at deploy time).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _int_env(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back safely."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    air_quality_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"
    http_timeout_seconds: float = 8.0
    cache_ttl_seconds: int = field(default_factory=lambda: _int_env("CACHE_TTL_SECONDS", 600))
    cache_max_entries: int = 256
    rate_limit_per_minute: int = field(
        default_factory=lambda: _int_env("RATE_LIMIT_PER_MINUTE", 30)
    )
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
        )
    )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (one per process)."""
    return Settings()
