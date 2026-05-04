"""Solar forecast utilities for EV Charger Manager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_ts(ts: Any) -> datetime | None:
    """Parse a timestamp string or datetime into a UTC-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if not isinstance(ts, str):
        return None
    from homeassistant.util import dt as dt_util
    return dt_util.parse_datetime(ts)


def read_solar_forecast(attributes: dict[str, Any]) -> dict[datetime, float]:
    """Extract a {datetime: kW} forecast from an Open-Meteo Solar Forecast sensor's attributes.

    Tries the 'watts' key (W values) then 'wh_hours' (Wh/h values).
    Returns an empty dict if neither key is present.
    """
    result: dict[datetime, float] = {}

    watts = attributes.get("watts")
    if isinstance(watts, dict):
        for ts_str, val in watts.items():
            dt = _parse_ts(ts_str)
            if dt is not None:
                try:
                    result[dt] = float(val) / 1000.0
                except (TypeError, ValueError):
                    pass
        return result

    wh_hours = attributes.get("wh_hours")
    if isinstance(wh_hours, dict):
        for ts_str, val in wh_hours.items():
            dt = _parse_ts(ts_str)
            if dt is not None:
                try:
                    result[dt] = float(val) / 1000.0
                except (TypeError, ValueError):
                    pass

    return result


def solar_forecast_at(
    solar_forecast: dict[datetime, float],
    target: datetime,
    max_gap: timedelta = timedelta(hours=1),
) -> float | None:
    """Return the nearest forecast kW value for *target*, or None if outside max_gap.

    Comparison is done in UTC to avoid timezone issues.
    """
    if not solar_forecast:
        return None

    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    target_utc = target.astimezone(timezone.utc)

    best_dt = min(solar_forecast, key=lambda dt: abs((dt.astimezone(timezone.utc) - target_utc).total_seconds()))
    gap = abs((best_dt.astimezone(timezone.utc) - target_utc).total_seconds())
    if gap > max_gap.total_seconds():
        return None
    return solar_forecast[best_dt]


def build_effective_prices(
    today_prices: list[float],
    tomorrow_prices: list[float],
    solar_forecast: dict[datetime, float],
    export_price: float,
    min_charging_kw: float,
    slots_per_hour: int,
    today_start: datetime,
) -> list[float]:
    """Build an effective price list substituting export_price for solar-covered slots.

    For each slot in today_prices + tomorrow_prices, if the solar forecast at that
    slot's time is >= min_charging_kw, the effective price is export_price (opportunity
    cost of not exporting), otherwise it's the import price.
    """
    slot_duration = timedelta(minutes=60 // slots_per_hour)
    effective: list[float] = []
    all_prices = today_prices + tomorrow_prices
    for i, price in enumerate(all_prices):
        slot_time = today_start + i * slot_duration
        solar_kw = solar_forecast_at(solar_forecast, slot_time) or 0.0
        if solar_kw >= min_charging_kw:
            effective.append(export_price)
        else:
            effective.append(price)
    return effective
