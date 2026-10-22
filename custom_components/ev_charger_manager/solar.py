"""Solar forecast utilities for EV Charger Manager."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def _parse_ts(ts: Any) -> datetime | None:
    """Parse a timestamp string or datetime into a UTC-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts
    if not isinstance(ts, str):
        return None
    return dt_util.parse_datetime(ts)


def read_solar_forecast(attributes: dict[str, Any]) -> dict[datetime, float]:
    """
    Extract a {datetime: kW} forecast from an Open-Meteo Solar Forecast sensor's attributes.

    Tries the 'watts' key (W values) then 'wh_hours' (Wh/h values).
    Returns an empty dict if neither key is present.
    """
    result: dict[datetime, float] = {}

    watts = attributes.get("watts")
    if isinstance(watts, dict):
        for ts_str, val in watts.items():
            dt = _parse_ts(ts_str)
            if dt is not None:
                with contextlib.suppress(TypeError, ValueError):
                    result[dt] = float(val) / 1000.0
        return result

    wh_hours = attributes.get("wh_hours")
    if isinstance(wh_hours, dict):
        for ts_str, val in wh_hours.items():
            dt = _parse_ts(ts_str)
            if dt is not None:
                with contextlib.suppress(TypeError, ValueError):
                    result[dt] = float(val) / 1000.0

    return result


def solar_forecast_at(
    solar_forecast: dict[datetime, float],
    target: datetime,
    max_gap: timedelta = timedelta(hours=1),
) -> float | None:
    """
    Return the nearest forecast kW value for *target*, or None if outside max_gap.

    Comparison is done in UTC to avoid timezone issues.
    """
    if not solar_forecast:
        return None

    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    target_utc = target.astimezone(UTC)

    best_dt = min(
        solar_forecast,
        key=lambda dt: abs((dt.astimezone(UTC) - target_utc).total_seconds()),
    )
    gap = abs((best_dt.astimezone(UTC) - target_utc).total_seconds())
    if gap > max_gap.total_seconds():
        return None
    return solar_forecast[best_dt]
