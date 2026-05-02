"""Charge strategy implementations for each charge mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChargeDecision:
    """Result of a charge strategy calculation."""

    target_current: float  # Amperes; 0 means pause/stop charging
    reason: str


def strategy_asap(max_current: float) -> ChargeDecision:
    """Always charge at maximum available current."""
    return ChargeDecision(
        target_current=max_current,
        reason=f"ASAP – charging at {max_current:.0f} A",
    )


def strategy_solar_excess(
    solar_power_kw: float,
    min_current: float,
    max_current: float,
    phases: int,
    voltage: float,
    grid_export_kw: float | None = None,
    ev_charging_kw: float = 0.0,
) -> ChargeDecision:
    """Set current proportional to available solar excess.

    Args:
        solar_power_kw: Estimated PV output in kW (used when no grid sensor).
        min_current: Minimum charge current the charger accepts.
        max_current: Maximum allowed charge current.
        phases: Number of AC phases (1 or 3).
        voltage: Phase voltage in V (typically 230).
        grid_export_kw: If provided, use this as the available excess instead
            of the solar estimate.  Positive value means the site is currently
            exporting to the grid, i.e. available for EV charging.
        ev_charging_kw: Power currently drawn by the EV charger (kW).  The
            consumption sensor includes this load, so we add it back to recover
            the true available PV excess and avoid oscillation.
    """
    available_kw = (grid_export_kw if grid_export_kw is not None else solar_power_kw) + ev_charging_kw

    if available_kw <= 0:
        return ChargeDecision(
            target_current=0.0,
            reason=f"No solar excess available ({solar_power_kw:.2f} kW PV estimate)",
        )

    # P = V × I × phases  →  I = P / (V × phases)
    available_amps = (available_kw * 1000.0) / (phases * voltage)
    target = min(max_current, available_amps)

    if target < min_current:
        return ChargeDecision(
            target_current=0.0,
            reason=(
                f"Solar excess {available_kw:.2f} kW → {available_amps:.1f} A "
                f"is below minimum {min_current:.0f} A – pausing"
            ),
        )

    return ChargeDecision(
        target_current=round(target, 1),
        reason=(
            f"Solar excess {available_kw:.2f} kW → charging at {target:.1f} A"
        ),
    )


def strategy_solar_price_blend(
    solar_power_kw: float,
    min_current: float,
    max_current: float,
    phases: int,
    voltage: float,
    price_awareness: float,
    current_price: float,
    hourly_prices: list[float],
    charge_hours_needed: int,
    grid_export_kw: float | None = None,
    ev_charging_kw: float = 0.0,
) -> ChargeDecision:
    """Blend solar-excess and price-aware charging.

    Base current mirrors SOLAR_EXCESS. During the cheapest charge_hours_needed
    slots, the current is boosted toward max_current by a factor of
    price_awareness (0 = pure solar, 1 = full boost to max regardless of solar).

    Args:
        solar_power_kw: Estimated or measured PV output in kW.
        min_current: Minimum current the charger accepts.
        max_current: Maximum allowed current.
        phases: Number of AC phases.
        voltage: Phase voltage in V.
        price_awareness: Blend factor 0.0–1.0.
        current_price: Current spot price.
        hourly_prices: All available hourly prices for threshold calculation.
        charge_hours_needed: Number of cheap slots to target (already scaled for
            sub-hourly granularity).
        grid_export_kw: Measured grid export; takes priority over solar estimate.
        ev_charging_kw: Current EV draw included in the grid/consumption reading;
            added back to recover true available excess.
    """
    # Step 1 – solar baseline (same as SOLAR_EXCESS)
    available_kw = (grid_export_kw if grid_export_kw is not None else solar_power_kw) + ev_charging_kw
    solar_amps = min(max_current, max(0.0, (available_kw * 1000.0) / (phases * voltage)))

    # Step 2 – cheap-slot detection (same threshold as MINIMIZE_COST)
    in_cheap_slot = False
    threshold: float | None = None
    if hourly_prices and price_awareness > 0:
        n = max(1, min(charge_hours_needed, len(hourly_prices)))
        sorted_prices = sorted(p for p in hourly_prices if p is not None)
        if sorted_prices:
            threshold = sorted_prices[n - 1]
            in_cheap_slot = current_price <= threshold

    # Step 3 – blend toward max_current during cheap slots
    if in_cheap_slot:
        target = solar_amps + price_awareness * (max_current - solar_amps)
    else:
        target = solar_amps
    target = min(max_current, target)

    # Step 4 – min_current gate
    if target < min_current:
        slot_note = f"price {current_price:.4f} ≤ {threshold:.4f}, " if in_cheap_slot and threshold is not None else ""
        return ChargeDecision(
            target_current=0.0,
            reason=(
                f"Solar+price: {slot_note}{available_kw:.2f} kW → {target:.1f} A "
                f"below minimum {min_current:.0f} A – pausing"
            ),
        )

    if in_cheap_slot and threshold is not None:
        boost = target - solar_amps
        return ChargeDecision(
            target_current=round(target, 1),
            reason=(
                f"Solar+price: {available_kw:.2f} kW solar + {boost:.1f} A price boost "
                f"(price {current_price:.4f} ≤ {threshold:.4f}, awareness {price_awareness:.0%}) "
                f"→ {target:.1f} A"
            ),
        )

    return ChargeDecision(
        target_current=round(target, 1),
        reason=f"Solar+price: {available_kw:.2f} kW → {target:.1f} A (no cheap slot)",
    )


def strategy_minimize_cost(
    current_price: float,
    hourly_prices: list[float],
    min_current: float,
    max_current: float,
    charge_hours_needed: int,
    now: datetime | None = None,
) -> ChargeDecision:
    """Charge only during the cheapest hours of the available price window.

    Uses a simple threshold approach: collect today's + tomorrow's prices,
    sort them, pick the N cheapest, and charge at max current if the current
    hour falls within that set.

    Args:
        current_price: Current spot price (any currency/unit).
        hourly_prices: All available hourly prices (today + tomorrow).
        min_current: Minimum charge current.
        max_current: Maximum charge current.
        charge_hours_needed: How many cheap hours to target per day.
        now: Override for current time (used in tests).
    """
    if not hourly_prices:
        return ChargeDecision(
            target_current=max_current,
            reason="No price data – charging at max as fallback",
        )

    # The cheapest N distinct price levels serve as our threshold
    n = max(1, min(charge_hours_needed, len(hourly_prices)))
    sorted_prices = sorted(p for p in hourly_prices if p is not None)
    threshold = sorted_prices[n - 1]

    if current_price <= threshold:
        return ChargeDecision(
            target_current=max_current,
            reason=(
                f"Cheap hour – price {current_price:.4f} ≤ threshold {threshold:.4f}; "
                f"charging at {max_current:.0f} A"
            ),
        )

    return ChargeDecision(
        target_current=0.0,
        reason=(
            f"Expensive hour – price {current_price:.4f} > threshold {threshold:.4f}; "
            "pausing to save cost"
        ),
    )
