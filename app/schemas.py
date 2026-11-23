"""Pydantic request/response models. All user input is validated here."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthProfile(StrEnum):
    """Who the advice is for. Sensitive groups get stricter thresholds."""

    GENERAL = "general"
    CHILD = "child"
    ELDERLY = "elderly"
    PREGNANT = "pregnant"
    RESPIRATORY = "respiratory"  # asthma, COPD, etc.
    HEART = "heart"


class Activity(StrEnum):
    """What the user wants to do outdoors, ordered roughly by exertion."""

    COMMUTE = "commute"
    WALK = "walk"
    KIDS_PLAY = "kids_play"
    OUTDOOR_WORK = "outdoor_work"
    CYCLE = "cycle"
    RUN = "run"


class Language(StrEnum):
    """Languages the explanation can be written in."""

    ENGLISH = "en"
    HINDI = "hi"


class Verdict(StrEnum):
    """Overall go/no-go recommendation, from least to most restrictive."""

    GO = "go"
    CAUTION = "caution"
    LIMIT = "limit"
    AVOID = "avoid"


class UserContext(BaseModel):
    """Who is asking, what they plan to do and what they want to know.

    Grouping these keeps the rules engine and the explainer signatures short.
    """

    model_config = ConfigDict(frozen=True)

    profile: HealthProfile = HealthProfile.GENERAL
    activity: Activity = Activity.WALK
    duration_minutes: int = Field(default=30, ge=5, le=480)
    question: str | None = None
    language: Language = Language.ENGLISH


class AdviceRequest(BaseModel):
    """Input for /api/advice. Either a city name or coordinates is required."""

    city: str | None = Field(default=None, min_length=2, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    profile: HealthProfile = HealthProfile.GENERAL
    activity: Activity = Activity.WALK
    duration_minutes: int = Field(default=30, ge=5, le=480)
    question: str | None = Field(default=None, max_length=300)
    language: Language = Language.ENGLISH

    @field_validator("city", "question")
    @classmethod
    def _strip_and_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Drop control characters to keep logs and prompts clean.
        cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
        return cleaned or None

    @model_validator(mode="after")
    def _require_location(self) -> AdviceRequest:
        has_coords = self.latitude is not None and self.longitude is not None
        if not self.city and not has_coords:
            raise ValueError("Provide either a city or both latitude and longitude.")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must be provided together.")
        return self

    @property
    def context(self) -> UserContext:
        """The personal part of the request, without the location."""
        return UserContext(
            profile=self.profile,
            activity=self.activity,
            duration_minutes=self.duration_minutes,
            question=self.question,
            language=self.language,
        )


class Pollutants(BaseModel):
    """Current concentrations in µg/m³ (``None`` when the provider has no value)."""

    pm2_5: float | None = None
    pm10: float | None = None
    ozone: float | None = None
    nitrogen_dioxide: float | None = None
    sulphur_dioxide: float | None = None
    carbon_monoxide: float | None = None


class HourlyPoint(BaseModel):
    """One hour of the AQI forecast."""

    time: str
    aqi: int
    category: str | None = None  # AQI category label, so clients need no copy of the bands


class AirSnapshot(BaseModel):
    """Normalised air-quality reading for one location."""

    location_name: str
    latitude: float
    longitude: float
    timezone: str
    observed_at: str
    aqi: int
    pollutants: Pollutants
    forecast: list[HourlyPoint]


class AqiCategory(BaseModel):
    """US EPA AQI band for a reading."""

    level: int  # 1 (Good) .. 6 (Hazardous)
    label: str
    color: str
    health_message: str


class BestWindow(BaseModel):
    """A cleaner daytime window, as local ISO timestamps."""

    start: str
    end: str
    average_aqi: int


class Decision(BaseModel):
    """Deterministic, explainable recommendation from the rules engine."""

    verdict: Verdict
    headline: str
    risk_score: int = Field(ge=0, le=100)
    mask: str
    actions: list[str]
    indoor_tips: list[str]
    best_window: BestWindow | None
    dominant_pollutant: str | None


class AdviceResponse(BaseModel):
    """Everything the UI needs to render one recommendation."""

    air: AirSnapshot
    category: AqiCategory
    decision: Decision
    explanation: str
    ai_generated: bool
