"""Charge strategy implementations for each charge mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .planner import ENERGY_EPS_KWH, ChargePlan, future_capacity_below_price

if TYPE_CHECKING:
    from datetime import datetime, tzinfo


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


def opportunistic_price_is_eligible(
    current_import_price: float | None, price_threshold: float
) -> bool:
    """Whether the current all-in import price meets the opportunistic threshold."""
    return current_import_price is not None and current_import_price <= price_threshold


def opportunistic_kwh_remaining(
    daily_target_kwh: float,
    delivered_kwh: float,
    allowance_kwh: float,
    daily_remaining_kwh: float,
    ev_kwh_needed: float | None,
) -> float:
    """Return the unused extra allowance, bounded by remaining EV capacity."""
    already_extra = max(0.0, delivered_kwh - daily_target_kwh)
    remaining = max(0.0, allowance_kwh - already_extra)
    if ev_kwh_needed is not None:
        remaining = min(remaining, max(0.0, ev_kwh_needed - daily_remaining_kwh))
    return remaining


def opportunistic_energy_now(
    plan: ChargePlan,
    current_import_price: float,
    allowance_remaining_kwh: float,
) -> tuple[float, float]:
    """Split extra energy between now and forecast capacity at a lower cost."""
    future_cheaper_kwh = future_capacity_below_price(plan, current_import_price)
    return max(0.0, allowance_remaining_kwh - future_cheaper_kwh), future_cheaper_kwh


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
