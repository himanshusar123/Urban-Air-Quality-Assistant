import pytest

from app.schemas import Pollutants
from app.services.aqi import categorize, dominant_pollutant


@pytest.mark.parametrize(
    ("aqi", "level", "label"),
    [
        (0, 1, "Good"),
        (50, 1, "Good"),
        (51, 2, "Moderate"),
        (100, 2, "Moderate"),
        (101, 3, "Unhealthy for Sensitive Groups"),
        (150, 3, "Unhealthy for Sensitive Groups"),
        (151, 4, "Unhealthy"),
        (200, 4, "Unhealthy"),
        (201, 5, "Very Unhealthy"),
        (300, 5, "Very Unhealthy"),
        (301, 6, "Hazardous"),
        (999, 6, "Hazardous"),
    ],
)
def test_categorize_band_boundaries(aqi: int, level: int, label: str) -> None:
    category = categorize(aqi)
    assert category.level == level
    assert category.label == label
    assert category.color.startswith("#")


def test_categorize_clamps_negative_values() -> None:
    assert categorize(-5).label == "Good"


def test_dominant_pollutant_picks_highest_ratio() -> None:
    # PM2.5 at 90 is 2.6x its limit; PM10 at 160 is ~1.07x.
    assert dominant_pollutant(Pollutants(pm2_5=90, pm10=160)) == "pm2_5"


def test_dominant_pollutant_none_when_air_is_clean() -> None:
    assert dominant_pollutant(Pollutants(pm2_5=5, pm10=10, ozone=20)) is None


def test_dominant_pollutant_none_without_data() -> None:
    assert dominant_pollutant(Pollutants()) is None
