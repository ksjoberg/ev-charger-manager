"""Solar power estimation from location, time, and weather conditions.

Uses a standard solar geometry model (declination + hour angle) to compute the
sun's elevation angle, then attenuates the peak PV output by the sine of that
angle and a weather-derived cloud-cover factor.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .const import WEATHER_ATTENUATION, WEATHER_ATTENUATION_DEFAULT


def _solar_elevation_deg(latitude_deg: float, longitude_deg: float, dt: datetime) -> float:
    """Return the sun's elevation angle in degrees for the given location and time.

    Uses the Spencer equation for declination and the equation of time so that
    no external astronomy library is required.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    utc_hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    day_of_year = dt.timetuple().tm_yday

    # Solar declination (Spencer, 1971)
    b = math.radians(360.0 / 365.0 * (day_of_year - 81))
    declination = math.radians(23.45 * math.sin(b))

    # Equation of time in minutes (approximate)
    eot_minutes = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    # Apparent solar time
    solar_time = utc_hour + longitude_deg / 15.0 + eot_minutes / 60.0

    # Hour angle: 0 at solar noon, ±180° at midnight
    hour_angle = math.radians(15.0 * (solar_time - 12.0))

    lat = math.radians(latitude_deg)
    sin_elevation = (
        math.sin(lat) * math.sin(declination)
        + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    )

    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation))))


def estimate_solar_power_kw(
    peak_power_kw: float,
    latitude: float,
    longitude: float,
    weather_condition: str,
    dt: datetime | None = None,
) -> float:
    """Estimate instantaneous PV output in kW.

    Args:
        peak_power_kw: Nameplate capacity of the PV system (kWp).
        latitude: Site latitude in decimal degrees.
        longitude: Site longitude in decimal degrees.
        weather_condition: HA weather state string (e.g. "sunny", "partlycloudy").
        dt: Moment to evaluate; defaults to now (UTC).

    Returns:
        Estimated output in kW, clamped to [0, peak_power_kw].
    """
    if peak_power_kw <= 0:
        return 0.0

    if dt is None:
        dt = datetime.now(tz=timezone.utc)

    elevation = _solar_elevation_deg(latitude, longitude, dt)

    if elevation <= 0.0:
        return 0.0

    # Irradiance fraction proportional to sine of elevation (Lambert's cosine law)
    irradiance_factor = math.sin(math.radians(elevation))

    condition_key = weather_condition.lower().replace(" ", "-")
    attenuation = WEATHER_ATTENUATION.get(condition_key, WEATHER_ATTENUATION_DEFAULT)

    result = peak_power_kw * irradiance_factor * attenuation
    return max(0.0, min(peak_power_kw, result))
