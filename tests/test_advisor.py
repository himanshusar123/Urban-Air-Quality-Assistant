import pytest

from app.schemas import Activity, HealthProfile
from app.services.advisor import (
    best_window,
    decide,
    duration_factor,
    mask_advice,
    risk_score,
    verdict_for,
)
from tests.conftest import make_air, make_forecast


def test_clean_air_is_go_for_healthy_walker() -> None:
    decision = decide(make_air(aqi=35), HealthProfile.GENERAL, Activity.WALK, 30)
    assert decision.verdict == "go"
    assert decision.mask == "No mask needed for air quality."


def test_sensitive_runner_gets_stricter_advice_than_healthy_runner() -> None:
    air = make_air(aqi=120)
    healthy = decide(air, HealthProfile.GENERAL, Activity.RUN, 45)
    asthmatic = decide(air, HealthProfile.RESPIRATORY, Activity.RUN, 45)
    assert asthmatic.risk_score > healthy.risk_score
    order = ["go", "caution", "limit", "avoid"]
    assert order.index(asthmatic.verdict) > order.index(healthy.verdict)
    assert "inhaler" not in " ".join(healthy.actions)


def test_higher_exertion_increases_risk() -> None:
    walk = risk_score(150, HealthProfile.GENERAL, Activity.WALK, 60)
    run = risk_score(150, HealthProfile.GENERAL, Activity.RUN, 60)
    assert run > walk


def test_longer_duration_increases_risk() -> None:
    assert duration_factor(20) < duration_factor(60) < duration_factor(90) < duration_factor(300)


def test_risk_score_is_bounded() -> None:
    assert risk_score(10_000, HealthProfile.HEART, Activity.RUN, 480) == 100
    assert risk_score(-10, HealthProfile.GENERAL, Activity.COMMUTE, 5) == 0


def test_hazardous_air_is_always_avoid() -> None:
    assert verdict_for(score=1, aqi=350) == "avoid"


@pytest.mark.parametrize(
    ("score", "expected"), [(0, "go"), (24, "go"), (25, "caution"), (45, "limit"), (70, "avoid")]
)
def test_verdict_thresholds(score: int, expected: str) -> None:
    assert verdict_for(score, aqi=100) == expected


def test_mask_advice_levels() -> None:
    assert "No mask" in mask_advice(40, sensitive=False)
    assert "Optional" in mask_advice(70, sensitive=True)
    assert "N95" in mask_advice(120, sensitive=True)
    assert "No mask" in mask_advice(120, sensitive=False)
    assert "strongly" in mask_advice(250, sensitive=False)


def test_best_window_finds_cleanest_daytime_slot() -> None:
    forecast = make_forecast(8, [170, 160, 120, 90, 80, 95, 150, 170])
    window = best_window(forecast, 60)
    assert window is not None
    assert window.start.endswith("12:00")
    assert window.average_aqi == 80


def test_best_window_respects_duration() -> None:
    forecast = make_forecast(8, [170, 60, 200, 90, 90, 170])
    window = best_window(forecast, 120)  # needs 2 contiguous hours
    assert window is not None
    assert window.start.endswith("11:00")
    assert window.end.endswith("13:00")


def test_best_window_ignores_night_hours() -> None:
    forecast = make_forecast(0, [10, 10, 10, 10, 200, 200, 150])  # 00:00-06:00
    window = best_window(forecast, 60)
    assert window is not None
    assert window.start.endswith("06:00")


def test_best_window_none_when_no_data() -> None:
    assert best_window([], 30) is None


def test_decide_only_suggests_meaningfully_cleaner_window() -> None:
    flat = make_air(aqi=150, forecast=make_forecast(8, [148, 149, 150, 151]))
    assert decide(flat, HealthProfile.GENERAL, Activity.WALK, 30).best_window is None

    improving = make_air(aqi=150, forecast=make_forecast(8, [150, 120, 70, 90]))
    window = decide(improving, HealthProfile.GENERAL, Activity.WALK, 30).best_window
    assert window is not None and window.average_aqi == 70


def test_decide_reports_dominant_pollutant_and_indoor_tips() -> None:
    decision = decide(make_air(aqi=210, pm2_5=120.0), HealthProfile.CHILD, Activity.KIDS_PLAY, 60)
    assert decision.verdict == "avoid"
    assert decision.dominant_pollutant == "pm2_5"
    assert any("HEPA" in tip for tip in decision.indoor_tips)
    assert any("incense" in tip for tip in decision.indoor_tips)


def test_limit_verdict_suggests_indoor_workout_for_runners() -> None:
    decision = decide(make_air(aqi=100), HealthProfile.RESPIRATORY, Activity.RUN, 30)
    assert decision.verdict == "limit"
    assert any("indoor session" in action for action in decision.actions)
