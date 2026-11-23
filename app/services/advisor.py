"""Rule-based decision engine.

Turns an air-quality snapshot plus the user's context (health profile,
activity, duration) into a transparent, reproducible recommendation.
Gemini is only used *afterwards* to explain this decision in plain language,
so safety-relevant logic never depends on a generative model.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.schemas import (
    Activity,
    AirSnapshot,
    BestWindow,
    Decision,
    HealthProfile,
    HourlyPoint,
    UserContext,
    Verdict,
)
from app.services.aqi import categorize, dominant_pollutant

# How much more a group is affected than a healthy adult.
SENSITIVITY: dict[HealthProfile, float] = {
    HealthProfile.GENERAL: 1.0,
    HealthProfile.CHILD: 1.3,
    HealthProfile.ELDERLY: 1.3,
    HealthProfile.PREGNANT: 1.3,
    HealthProfile.RESPIRATORY: 1.5,
    HealthProfile.HEART: 1.5,
}

# Relative breathing rate (and therefore pollutant dose) per activity.
EXERTION: dict[Activity, float] = {
    Activity.COMMUTE: 0.8,
    Activity.WALK: 0.9,
    Activity.KIDS_PLAY: 1.1,
    Activity.OUTDOOR_WORK: 1.2,
    Activity.CYCLE: 1.25,
    Activity.RUN: 1.4,
}

ACTIVITY_LABEL: dict[Activity, str] = {
    Activity.COMMUTE: "commuting",
    Activity.WALK: "walking",
    Activity.KIDS_PLAY: "outdoor play",
    Activity.OUTDOOR_WORK: "outdoor work",
    Activity.CYCLE: "cycling",
    Activity.RUN: "running",
}

# Waking hours considered when suggesting a better time to go out.
DAYTIME_START_HOUR = 6
DAYTIME_END_HOUR = 21

# Risk-score upper bounds (exclusive) for each verdict; anything above is "avoid".
VERDICT_THRESHOLDS: tuple[tuple[int, Verdict], ...] = (
    (25, Verdict.GO),
    (45, Verdict.CAUTION),
    (70, Verdict.LIMIT),
)
HAZARDOUS_AQI = 300  # always "avoid", whatever the personal risk score says
MAX_AQI = 500  # upper end of the US AQI scale
# Only suggest another time if it is at least this much cleaner than right now.
MIN_WINDOW_IMPROVEMENT = 10

# Mask guidance thresholds (US AQI).
MASK_STRONG_AQI = 200
MASK_RECOMMENDED_AQI = 150
MASK_SENSITIVE_AQI = 100
MASK_OPTIONAL_AQI = 50


def duration_factor(minutes: int) -> float:
    """Longer exposure means a larger dose."""
    if minutes <= 30:
        return 0.85
    if minutes <= 60:
        return 1.0
    if minutes <= 120:
        return 1.15
    return 1.3


def risk_score(aqi: int, profile: HealthProfile, activity: Activity, minutes: int) -> int:
    """Combine AQI, sensitivity, exertion and duration into a 0-100 score."""
    base = min(max(aqi, 0), MAX_AQI) / HAZARDOUS_AQI * 100
    score = base * SENSITIVITY[profile] * EXERTION[activity] * duration_factor(minutes)
    return int(round(min(100.0, score)))


def verdict_for(score: int, aqi: int) -> Verdict:
    """Map a risk score to a verdict. Hazardous air is always 'avoid'."""
    if aqi > HAZARDOUS_AQI:
        return Verdict.AVOID
    for threshold, verdict in VERDICT_THRESHOLDS:
        if score < threshold:
            return verdict
    return Verdict.AVOID


def mask_advice(aqi: int, sensitive: bool) -> str:
    """Say whether a mask is needed, and which kind actually helps."""
    if aqi > MASK_STRONG_AQI:
        return (
            "A well-fitted N95/FFP2 respirator is strongly recommended; "
            "cloth and surgical masks do not filter fine particles well."
        )
    if aqi > MASK_RECOMMENDED_AQI or (sensitive and aqi > MASK_SENSITIVE_AQI):
        return "Wear a well-fitted N95/FFP2 mask outdoors."
    if sensitive and aqi > MASK_OPTIONAL_AQI:
        return "Optional: carry an N95 mask in case conditions worsen."
    return "No mask needed for air quality."


def _parse_forecast(forecast: list[HourlyPoint]) -> list[tuple[datetime, int]]:
    """Parse forecast timestamps, silently skipping malformed ones."""
    parsed: list[tuple[datetime, int]] = []
    for point in forecast:
        try:
            parsed.append((datetime.fromisoformat(point.time), point.aqi))
        except ValueError:
            continue
    return parsed


def _is_contiguous(window: list[tuple[datetime, int]]) -> bool:
    """True if consecutive points are exactly one hour apart."""
    return all(
        later[0] - earlier[0] == timedelta(hours=1)
        for earlier, later in zip(window, window[1:], strict=False)
    )


def _is_daytime(start: datetime, end: datetime) -> bool:
    """True if the window starts after waking time and ends the same day before bedtime."""
    return (
        start.hour >= DAYTIME_START_HOUR
        and end.hour <= DAYTIME_END_HOUR
        and end.date() == start.date()
    )


def best_window(forecast: list[HourlyPoint], minutes: int) -> BestWindow | None:
    """Find the daytime window with the lowest average AQI.

    The window length equals the planned activity duration (rounded up to
    whole hours). Windows must be contiguous hourly points.
    """
    hours = max(1, math.ceil(minutes / 60))
    parsed = _parse_forecast(forecast)

    best: tuple[float, datetime, datetime] | None = None
    for i in range(len(parsed) - hours + 1):
        window = parsed[i : i + hours]
        start, end = window[0][0], window[-1][0] + timedelta(hours=1)
        if not (_is_contiguous(window) and _is_daytime(start, end)):
            continue
        avg = sum(aqi for _, aqi in window) / hours
        if best is None or avg < best[0]:
            best = (avg, start, end)

    if best is None:
        return None
    avg, start, end = best
    return BestWindow(
        start=start.isoformat(timespec="minutes"),
        end=end.isoformat(timespec="minutes"),
        average_aqi=round(avg),
    )


def _actions(verdict: Verdict, activity: Activity, sensitive: bool) -> list[str]:
    label = ACTIVITY_LABEL[activity]
    if verdict is Verdict.GO:
        return [
            f"Conditions are fine for {label}. Enjoy it!",
            "Stay hydrated and check again if your plans change by several hours.",
        ]
    if verdict is Verdict.CAUTION:
        actions = [
            f"{label.capitalize()} is okay, but keep the intensity moderate.",
            "Prefer parks and side streets over busy main roads.",
        ]
        if sensitive:
            actions.append("Keep any prescribed inhaler or medication with you.")
        return actions
    if verdict is Verdict.LIMIT:
        actions = [
            f"Shorten your {label} or move it indoors if possible.",
            "Avoid traffic-heavy roads and rush hours (8–10 am, 6–9 pm).",
            "Take breaks and stop if you notice coughing or breathlessness.",
        ]
        if activity in (Activity.RUN, Activity.CYCLE):
            actions.insert(1, "Swap the workout for an indoor session today.")
        return actions
    actions = [
        f"Avoid outdoor {label} right now.",
        "If you must go out, keep it short and travel by car or metro with windows closed.",
    ]
    if sensitive:
        actions.append("Seek medical help if symptoms like chest tightness or wheezing appear.")
    return actions


def _indoor_tips(level: int) -> list[str]:
    """Home advice by AQI category level (1 = Good .. 6 = Hazardous)."""
    tips = ["Ventilate your home when the AQI is lowest, usually early afternoon."]
    if level >= 3:
        tips = [
            "Keep windows and doors closed during peak pollution hours.",
            "Run an air purifier with a HEPA filter in the room you use most.",
        ]
    if level >= 4:
        tips.append("Avoid indoor smoke sources such as incense, candles and frying.")
    return tips


def _headline(verdict: Verdict, activity: Activity) -> str:
    label = ACTIVITY_LABEL[activity]
    return {
        Verdict.GO: f"Good to go for {label}",
        Verdict.CAUTION: f"{label.capitalize()} is okay with some care",
        Verdict.LIMIT: f"Limit {label} today",
        Verdict.AVOID: f"Skip outdoor {label} for now",
    }[verdict]


def decide(air: AirSnapshot, ctx: UserContext) -> Decision:
    """Produce the full recommendation for this user and moment."""
    sensitive = ctx.profile is not HealthProfile.GENERAL
    score = risk_score(air.aqi, ctx.profile, ctx.activity, ctx.duration_minutes)
    verdict = verdict_for(score, air.aqi)
    level = categorize(air.aqi).level
    window = best_window(air.forecast, ctx.duration_minutes)
    if window is not None and window.average_aqi > air.aqi - MIN_WINDOW_IMPROVEMENT:
        window = None

    return Decision(
        verdict=verdict,
        headline=_headline(verdict, ctx.activity),
        risk_score=score,
        mask=mask_advice(air.aqi, sensitive),
        actions=_actions(verdict, ctx.activity, sensitive),
        indoor_tips=_indoor_tips(level),
        best_window=window,
        dominant_pollutant=dominant_pollutant(air.pollutants),
    )
