"""Charge strategy implementations for each charge mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .planner import ENERGY_EPS_KWH

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from .planner import ChargePlan


@dataclass
class ChargeDecision:
    """Result of a charge strategy calculation."""

    target_current: float  # Amperes; 0 means pause/stop charging
    reason: str


def strategy_asap(max_current: float) -> ChargeDecision:
    """Charge at the maximum available current."""
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
    """
    Set current proportional to available solar excess.

    Args:
        solar_power_kw: Measured or estimated PV output in kW.
        min_current: Minimum charge current the charger accepts.
        max_current: Maximum allowed charge current.
        phases: Number of AC phases (1 or 3).
        voltage: Phase voltage in V (typically 230).
        grid_export_kw: If provided, use this as the available excess instead
            of the solar estimate.  Positive value means the site is currently
            exporting to the grid, i.e. available for EV charging.
        ev_charging_kw: Power currently drawn by the EV charger (kW).  Since
            grid_export_kw is measured after all loads (including current EV draw),
            we add it back to get total available capacity for EV charging.

    """
    # Total capacity available for EV charging:
    # - With grid sensor: current export + what EV is already using
    # - Without grid sensor: solar estimate + what EV is already using
    # This allows the charger to ramp up as more solar becomes available.
    available_kw = (
        grid_export_kw if grid_export_kw is not None else solar_power_kw
    ) + ev_charging_kw

    # Safety check: When both grid export and measured solar are available,
    # cap at solar production to prevent grid import due to measurement timing issues.
    if grid_export_kw is not None and solar_power_kw > 0:
        available_kw = min(available_kw, solar_power_kw)

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
        reason=(f"Solar excess {available_kw:.2f} kW → charging at {target:.1f} A"),
    )


def _net_cost_rank(
    hourly_prices: list[float],
    hourly_solar_forecast: list[float] | None,
    charge_hours_needed: int,
    current_price: float,
    charging_kw: float,
) -> tuple[bool, float | None]:
    """
    Rank hours by net grid-import cost and return whether hour 0 is cheap.

    When a solar forecast is available, each hour's cost is weighted by how
    much of the charging power must be imported from the grid after solar
    covers its share. When no forecast is provided, falls back to pure price
    threshold.

    Returns (in_cheap_slot, threshold_or_None).
    """
    if not hourly_prices:
        return False, None

    if hourly_solar_forecast:
        # Net cost = grid_import_kw * price, where grid_import = max(0, charging - solar)
        def _net_cost(i: int) -> float:
            price = (
                hourly_prices[i]
                if i < len(hourly_prices)
                else (hourly_prices[-1] if hourly_prices else 0.0)
            )
            solar = hourly_solar_forecast[i] if i < len(hourly_solar_forecast) else 0.0
            return max(0.0, charging_kw - solar) * price

        n = max(1, min(charge_hours_needed, len(hourly_prices)))
        ranked = sorted(range(len(hourly_prices)), key=_net_cost)
        cheap_set = set(ranked[:n])
        in_cheap = 0 in cheap_set
        # For reason string: effective cost at hour 0
        cost_now = _net_cost(0)
        cost_threshold = _net_cost(ranked[n - 1]) if n <= len(ranked) else cost_now
        # Use a pseudo-threshold for the reason message
        return in_cheap, cost_threshold
    n = max(1, min(charge_hours_needed, len(hourly_prices)))
    sorted_prices = sorted(p for p in hourly_prices if p is not None)
    if not sorted_prices:
        return False, None
    threshold = sorted_prices[n - 1]
    return current_price <= threshold, threshold


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
    hourly_solar_forecast: list[float] | None = None,
) -> ChargeDecision:
    """
    Blend solar-excess and price-aware charging.

    Base current mirrors SOLAR_EXCESS. During the cheapest charge_hours_needed
    slots, the current is boosted toward max_current by a factor of
    price_awareness (0 = pure solar, 1 = full boost to max regardless of solar).

    When hourly_solar_forecast is provided, slot selection uses net grid-import
    cost (charging_kw - solar_kw) * price, preferring hours where solar covers
    most of the load. Without a forecast, falls back to pure price threshold.

    Args:
        solar_power_kw: Estimated or measured PV output in kW.
        min_current: Minimum current the charger accepts.
        max_current: Maximum allowed current.
        phases: Number of AC phases.
        voltage: Phase voltage in V.
        price_awareness: Blend factor 0.0–1.0.
        current_price: Current spot price.
        hourly_prices: Future hourly prices aligned to current hour = index 0.
        charge_hours_needed: Number of cheap slots to target (already scaled for
            sub-hourly granularity).
        grid_export_kw: Measured grid export; takes priority over solar estimate.
        ev_charging_kw: Current EV draw; added back to recover total available
            capacity (grid export is measured after all loads including EV).
        hourly_solar_forecast: Per-hour available-kW forecast aligned to current
            hour = index 0. When provided, enables net-cost slot ranking.

    """
    # Step 1 – solar baseline (same as SOLAR_EXCESS)
    available_kw = (
        grid_export_kw if grid_export_kw is not None else solar_power_kw
    ) + ev_charging_kw

    # Safety check: When both grid export and measured solar are available,
    # cap at solar production to prevent grid import due to measurement timing issues.
    if grid_export_kw is not None and solar_power_kw > 0:
        available_kw = min(available_kw, solar_power_kw)

    solar_amps = min(
        max_current, max(0.0, (available_kw * 1000.0) / (phases * voltage))
    )

    # Step 2 – cheap-slot detection
    in_cheap_slot = False
    threshold: float | None = None
    if price_awareness > 0:
        charging_kw = (max_current * phases * voltage) / 1000.0
        in_cheap_slot, threshold = _net_cost_rank(
            hourly_prices,
            hourly_solar_forecast,
            charge_hours_needed,
            current_price,
            charging_kw,
        )

    # Step 3 – blend toward max_current during cheap slots
    if in_cheap_slot:
        target = solar_amps + price_awareness * (max_current - solar_amps)
    else:
        target = solar_amps
    target = min(max_current, target)

    # Step 4 – min_current gate
    if target < min_current:
        slot_note = (
            f"price {current_price:.4f} ≤ {threshold:.4f}, "
            if in_cheap_slot and threshold is not None
            else ""
        )
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
    phases: int = 1,
    voltage: float = 230.0,
    hourly_solar_forecast: list[float] | None = None,
    solar_excess_kw: float = 0.0,
    export_price: float | None = None,
    now: datetime | None = None,  # noqa: ARG001
) -> ChargeDecision:
    """
    Charge only during the cheapest hours of the available price window.

    When hourly_solar_forecast is provided, slot selection uses net grid-import
    cost (max(0, charging_kw - solar_kw) * price) so hours with high solar
    coverage are preferred even at higher nominal prices.

    When solar_excess_kw >= min_charging_kw and export_price is set, the
    effective cost of charging right now is export_price (opportunity cost),
    which is used for the current-slot decision.

    Args:
        current_price: Current import spot price.
        hourly_prices: Future hourly prices aligned to current hour = index 0.
        min_current: Minimum charge current.
        max_current: Maximum charge current.
        charge_hours_needed: How many cheap hours to target per day.
        phases: Number of AC phases (used for net-cost calc when forecast given).
        voltage: Phase voltage in V.
        hourly_solar_forecast: Per-hour available-kW forecast aligned to current
            hour = index 0. When provided, enables net-cost slot ranking.
        solar_excess_kw: Real-time solar/grid export available (kW).
        export_price: Current grid export price; used as opportunity cost when solar covers load.
        now: Override for current time (used in tests).

    """
    if not hourly_prices:
        return ChargeDecision(
            target_current=max_current,
            reason="No price data – charging at max as fallback",
        )

    min_charging_kw = (min_current * phases * voltage) / 1000.0
    solar_covering = solar_excess_kw >= min_charging_kw and export_price is not None
    effective_price_now = export_price if solar_covering else current_price

    charging_kw = (max_current * phases * voltage) / 1000.0
    in_cheap_slot, threshold = _net_cost_rank(
        hourly_prices,
        hourly_solar_forecast,
        charge_hours_needed,
        effective_price_now,
        charging_kw,
    )

    if in_cheap_slot:
        if solar_covering:
            return ChargeDecision(
                target_current=max_current,
                reason=(
                    f"Solar covers load ({solar_excess_kw:.2f} kW excess); "
                    f"effective cost {effective_price_now:.4f} ≤ threshold {threshold:.4f}; "
                    f"charging at {max_current:.0f} A"
                ),
            )
        if hourly_solar_forecast and threshold is not None:
            solar_now = hourly_solar_forecast[0] if hourly_solar_forecast else 0.0
            return ChargeDecision(
                target_current=max_current,
                reason=(
                    f"Cheap+solar hour – net cost threshold {threshold:.4f}; "
                    f"solar {solar_now:.2f} kW; charging at {max_current:.0f} A"
                ),
            )
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


def _hhmm(dt: datetime, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def _span(plan: ChargePlan, tz: tzinfo) -> str:
    """Time span from the first to the last planned charging block."""
    if not plan.blocks:
        return ""
    return f" {_hhmm(plan.blocks[0]['start'], tz)}–{_hhmm(plan.blocks[-1]['end'], tz)}"


def _today_text(plan: ChargePlan, tz: tzinfo) -> str:
    """Summarise the rest of the current window's plan."""
    parts = []
    if plan.planned_kwh > ENERGY_EPS_KWH:
        parts.append(
            f"{plan.planned_kwh:.1f} kWh{_span(plan, tz)}, est. {plan.cost:.2f}"
        )
    if plan.deferred_kwh > ENERGY_EPS_KWH:
        saving = plan.avoided_cost - plan.deferred_cost
        parts.append(
            f"{plan.deferred_kwh:.1f} kWh deferred to tomorrow, saves {saving:.2f}"
        )
    if plan.shortfall_kwh > ENERGY_EPS_KWH:
        deadline = _hhmm(plan.deadline, tz) if plan.deadline else "deadline"
        parts.append(f"{plan.shortfall_kwh:.1f} kWh won't fit before {deadline}")
    return "Today: " + "; ".join(parts)


def _tomorrow_text(
    next_plan: ChargePlan | None, window_end: datetime, carried_kwh: float, tz: tzinfo
) -> str:
    """Summarise the next window's plan over its published prices."""
    if next_plan is None or not next_plan.slots:
        return "Tomorrow: prices not published yet"
    carried = (
        f" (incl. {carried_kwh:.1f} deferred)" if carried_kwh > ENERGY_EPS_KWH else ""
    )
    coverage = (
        f", prices until {_hhmm(next_plan.deadline, tz)}"
        if next_plan.deadline and next_plan.deadline < window_end
        else ""
    )
    return (
        f"Tomorrow: {next_plan.requested_kwh:.1f} kWh{carried}"
        f"{_span(next_plan, tz)}, est. {next_plan.cost:.2f}{coverage}"
    )


def strategy_fixed_daily_amount(
    plan: ChargePlan,
    min_current: float,
    max_current: float,
    phases: int,
    voltage: float,
    tz: tzinfo,
    *,
    next_plan: ChargePlan | None = None,
    next_window_end: datetime | None = None,
) -> ChargeDecision:
    """
    Execute the current slot of a fixed-amount charge plan.

    The plan is rebuilt on every update for the energy still missing, so this
    only has to translate the current slot's planned energy into a current.

    Args:
        plan: Plan whose first slot is the remainder of the current slot.
        min_current: Minimum charge current the charger accepts.
        max_current: Maximum allowed charge current.
        phases: Number of AC phases.
        voltage: Phase voltage in V.
        tz: Time zone used for times in the reason string.
        next_plan: Plan for the next window over its published prices, or
            None when they are not published yet.
        next_window_end: End of the next window; when given, the reason
            also describes the next window's plan.

    """
    tomorrow = (
        f". {_tomorrow_text(next_plan, next_window_end, plan.deferred_kwh, tz)}"
        if next_window_end is not None
        else ""
    )

    if plan.requested_kwh <= ENERGY_EPS_KWH:
        return ChargeDecision(
            target_current=0.0,
            reason=f"Fixed daily: daily amount reached{tomorrow}",
        )

    outlook = f"{_today_text(plan, tz)}{tomorrow}"
    watts_per_amp = phases * voltage
    min_kw = min_current * watts_per_amp / 1000.0
    max_kw = max_current * watts_per_amp / 1000.0
    current = plan.current
    power_kw = current.charge_power_kw(min_kw, max_kw) if current else 0.0

    if power_kw <= 0 or current is None:
        nxt = plan.next_charge()
        when = (
            f"next charge {_hhmm(nxt.slot.start, tz)}" if nxt else "no charging planned"
        )
        return ChargeDecision(
            target_current=0.0,
            reason=f"Fixed daily: waiting, {when}. {outlook}",
        )

    amps = min(max_current, power_kw * 1000.0 / watts_per_amp)
    return ChargeDecision(
        target_current=round(amps, 1),
        reason=f"Fixed daily: charging {amps:.0f} A. {outlook}",
    )
