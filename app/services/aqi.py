"""Pure AQI helpers: category bands and dominant-pollutant detection.

Bands follow the US EPA AQI scale (the scale returned by Open-Meteo's
``us_aqi`` field). Functions here are side-effect free and fully unit-tested.
"""

from __future__ import annotations

from app.schemas import AqiCategory, Pollutants

# (upper bound inclusive, level, label, colour, message)
_BANDS: tuple[tuple[int, int, str, str, str], ...] = (
    (50, 1, "Good", "#2e7d32", "Air quality is satisfactory and poses little or no risk."),
    (
        100,
        2,
        "Moderate",
        "#b58900",
        "Acceptable; unusually sensitive people should consider reducing prolonged exertion.",
    ),
    (
        150,
        3,
        "Unhealthy for Sensitive Groups",
        "#b34700",
        "Children, older adults and people with heart or lung conditions may be affected.",
    ),
    (
        200,
        4,
        "Unhealthy",
        "#c62828",
        "Everyone may begin to feel effects; sensitive groups may feel serious effects.",
    ),
    (
        300,
        5,
        "Very Unhealthy",
        "#6a1b9a",
        "Health alert: the risk of effects is increased for everyone.",
    ),
    (10_000, 6, "Hazardous", "#4a0e0e", "Emergency conditions: everyone is likely to be affected."),
)

# Reference limits in µg/m³ (WHO 2021 / EPA short-term values) used only to
# rank which pollutant is furthest above a safe level.
_REFERENCE_LIMITS: dict[str, float] = {
    "pm2_5": 35.0,
    "pm10": 150.0,
    "ozone": 100.0,
    "nitrogen_dioxide": 200.0,
    "sulphur_dioxide": 40.0,
    "carbon_monoxide": 4000.0,
}

POLLUTANT_NAMES: dict[str, str] = {
    "pm2_5": "PM2.5 (fine particles)",
    "pm10": "PM10 (coarse dust)",
    "ozone": "Ozone (O₃)",
    "nitrogen_dioxide": "Nitrogen dioxide (NO₂)",
    "sulphur_dioxide": "Sulphur dioxide (SO₂)",
    "carbon_monoxide": "Carbon monoxide (CO)",
}


def categorize(aqi: int) -> AqiCategory:
    """Map a US AQI value to its category. Negative values are clamped to 0."""
    value = max(0, aqi)
    for upper, level, label, color, message in _BANDS:
        if value <= upper:
            return AqiCategory(level=level, label=label, color=color, health_message=message)
    # Unreachable because the last band is effectively unbounded.
    raise AssertionError("AQI band table is incomplete")  # pragma: no cover


def dominant_pollutant(pollutants: Pollutants) -> str | None:
    """Return the key of the pollutant with the highest ratio to its limit.

    Returns ``None`` when no pollutant data is available or every value is
    comfortably below its reference limit (ratio < 0.5).
    """
    best_key: str | None = None
    best_ratio = 0.0
    for key, limit in _REFERENCE_LIMITS.items():
        value = getattr(pollutants, key)
        if value is None:
            continue
        ratio = value / limit
        if ratio > best_ratio:
            best_key, best_ratio = key, ratio
    return best_key if best_ratio >= 0.5 else None
