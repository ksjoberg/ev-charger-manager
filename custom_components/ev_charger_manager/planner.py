"""
Cost-optimal planner for delivering a fixed amount of energy to the EV.

The planning horizon is split into price slots (typically 15 min or 1 h, as
published by Nordpool).  For every slot we know:

* the all-in import price (what one more kWh from the grid costs),
* the all-in export price (what one kWh of solar would earn if exported),
* the forecast solar surplus (PV production minus household base load).

Charging power drawn from the solar surplus costs the *export* price (lost
export revenue); power beyond the surplus costs the *import* price.  The cost
of charging at power ``e`` during a slot is therefore piecewise linear and
convex in ``e``.  Because the charger cannot run below ``min_kw`` (but may be
switched off part of the slot), the achievable cost curve for a slot is the
lower convex hull of that function over ``0`` and ``[min_kw, max_kw]``.

With convex per-slot cost curves and a single "total energy" constraint, a
greedy fill of the cheapest marginal kWh across all slots is optimal.  This
naturally handles cases where exporting solar now and importing later is
cheaper, because the comparison is always export price now vs import price
later.

Once the next window's prices are published, energy can be deferred to it:
the next window first reserves its own daily amount, and each remaining kWh
it could still take competes with today's kWh at its price divided by
``1 - min_saving``.  Today's most expensive kWh are thus given up whenever
the next window can supply them at least ``min_saving`` cheaper.
"""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

ENERGY_EPS_KWH = 0.005

TODAY_PRICE_KEYS = ("raw_today", "today", "prices_today", "prices")
TOMORROW_PRICE_KEYS = ("raw_tomorrow", "tomorrow", "prices_tomorrow")

DEFAULT_SLOT = timedelta(minutes=15)
# Solar samples further than this from a slot are treated as "no production".
MAX_SOLAR_GAP = timedelta(hours=1)
# Fallback prices when no price sensors are configured: prefer solar, then grid ASAP.
FALLBACK_IMPORT_PRICE = 1.0
FALLBACK_EXPORT_PRICE = 0.0


# ----------------------------------------------------------------------
# Input parsing
# ----------------------------------------------------------------------


def to_utc(value: Any) -> datetime | None:
    """Parse a datetime or ISO string into a UTC-aware datetime."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class PriceInterval:
    """A price valid from start (inclusive) to end (exclusive)."""

    start: datetime
    end: datetime
    value: float


def _parse_price_mapping(raw: dict[Any, Any]) -> list[PriceInterval]:
    """
    Parse a ``{start: price}`` mapping, which may span several days.

    Each price lasts until the next start, capped at the shortest spacing
    between starts so gaps in the data stay uncovered.
    """
    parsed: list[tuple[datetime, float]] = []
    for key, value in raw.items():
        start = to_utc(key)
        if start is None or value is None:
            continue
        try:
            parsed.append((start, float(value)))
        except TypeError, ValueError:
            continue
    parsed.sort(key=lambda p: p[0])
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(parsed)]
    slot_len = min((g for g in gaps if g > timedelta(0)), default=DEFAULT_SLOT)
    intervals = []
    for i, (start, value) in enumerate(parsed):
        end = start + slot_len
        if i + 1 < len(parsed):
            end = min(end, parsed[i + 1][0])
        if end > start:
            intervals.append(PriceInterval(start, end, value))
    return intervals


def _parse_price_list(
    raw: Any, day_start: datetime, day_end: datetime
) -> list[PriceInterval]:
    """
    Parse one day's price list.

    Accepts Nordpool ``raw_*`` style dicts (``start``/``end``/``value``), plain
    lists of numbers spread evenly over the day, or a ``{start: price}``
    mapping (which may also cover the following day).
    """
    if isinstance(raw, dict):
        return _parse_price_mapping(raw)
    if not isinstance(raw, list) or not raw:
        return []

    if all(isinstance(item, dict) for item in raw):
        parsed: list[tuple[datetime, datetime | None, float]] = []
        for item in raw:
            start = to_utc(item.get("start", item.get("start_time")))
            end = to_utc(item.get("end", item.get("end_time")))
            value = item.get("value", item.get("price"))
            if start is None or value is None:
                continue
            try:
                parsed.append((start, end, float(value)))
            except TypeError, ValueError:
                continue
        parsed.sort(key=lambda p: p[0])
        default_len = (day_end - day_start) / max(1, len(raw))
        intervals = []
        for i, (start, raw_end, value) in enumerate(parsed):
            end = raw_end
            if end is None:
                end = parsed[i + 1][0] if i + 1 < len(parsed) else start + default_len
            intervals.append(PriceInterval(start, end, value))
        return intervals

    # Plain list: evenly spaced over the (possibly 23 h / 25 h) local day.
    slot_len = (day_end - day_start) / len(raw)
    intervals = []
    for i, value in enumerate(raw):
        if value is None:
            continue  # keep index alignment for missing values
        try:
            price = float(value)
        except TypeError, ValueError:
            continue
        start = day_start + i * slot_len
        intervals.append(PriceInterval(start, start + slot_len, price))
    return intervals


def parse_price_series(
    attrs: dict[str, Any],
    today_start: datetime,
    tomorrow_start: datetime,
    day_after_start: datetime,
) -> list[PriceInterval]:
    """
    Extract today's and tomorrow's prices as timestamped intervals (UTC).

    The day boundaries are local midnights; they are only used for plain number
    lists that carry no timestamps.
    """
    bounds = [to_utc(d) for d in (today_start, tomorrow_start, day_after_start)]
    if any(b is None for b in bounds):
        return []
    today, tomorrow, day_after = bounds  # type: ignore[misc]

    intervals: list[PriceInterval] = []
    for keys, day_start, day_end in (
        (TODAY_PRICE_KEYS, today, tomorrow),
        (TOMORROW_PRICE_KEYS, tomorrow, day_after),
    ):
        for key in keys:
            parsed = _parse_price_list(attrs.get(key), day_start, day_end)
            if parsed:
                intervals.extend(parsed)
                break

    intervals.sort(key=lambda iv: iv.start)
    deduped: list[PriceInterval] = []
    for iv in intervals:
        if deduped and iv.start < deduped[-1].end:
            continue
        deduped.append(iv)
    return deduped


def price_at(series: list[PriceInterval], t: datetime) -> float | None:
    """Return the price valid at *t*, or None if not covered."""
    for iv in series:
        if iv.start <= t < iv.end:
            return iv.value
    return None


def _interval_at(series: list[PriceInterval], t: datetime) -> PriceInterval | None:
    for iv in series:
        if iv.start <= t < iv.end:
            return iv
    return None


def solar_kw_between(
    samples: list[tuple[datetime, float]], start: datetime, end: datetime
) -> float:
    """
    Average forecast PV power (kW) for [start, end).

    *samples* must be sorted by time.  Uses the mean of samples inside the
    interval; falls back to linear interpolation at the midpoint.
    """
    if not samples:
        return 0.0
    times = [t for t, _ in samples]
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_left(times, end)
    if hi > lo:
        return sum(kw for _, kw in samples[lo:hi]) / (hi - lo)

    mid = start + (end - start) / 2
    idx = bisect.bisect_left(times, mid)
    if idx == 0:
        t0, kw0 = samples[0]
        return kw0 if t0 - mid <= MAX_SOLAR_GAP else 0.0
    if idx == len(samples):
        t1, kw1 = samples[-1]
        return kw1 if mid - t1 <= MAX_SOLAR_GAP else 0.0
    (t0, kw0), (t1, kw1) = samples[idx - 1], samples[idx]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return kw0
    frac = (mid - t0).total_seconds() / span
    return kw0 + frac * (kw1 - kw0)


# ----------------------------------------------------------------------
# Slots
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PlanSlot:
    """One planning interval with its prices and forecast solar surplus."""

    start: datetime
    end: datetime
    import_price: float
    export_price: float
    surplus_kw: float  # solar available for the EV (PV minus base load), >= 0
    estimated_price: bool = False

    @property
    def hours(self) -> float:
        """Return the slot length in hours."""
        return max(0.0, (self.end - self.start).total_seconds() / 3600.0)


def build_slots(
    now: datetime,
    deadline: datetime,
    import_series: list[PriceInterval],
    export_series: list[PriceInterval],
    solar_forecast: dict[datetime, float],
    base_load_kw: float,
    current_import_price: float | None = None,
    current_export_price: float | None = None,
) -> list[PlanSlot]:
    """
    Split [now, deadline) into price slots.

    Slots beyond the published prices use the price of the same time 24 h
    earlier (persistence forecast) and are flagged as estimated.  The first
    slot starts at *now* (it is the remainder of the current slot).
    """
    start_utc, deadline_utc = to_utc(now), to_utc(deadline)
    if start_utc is None or deadline_utc is None or start_utc >= deadline_utc:
        return []

    samples = sorted(
        (t, kw) for t, kw in ((to_utc(k), v) for k, v in solar_forecast.items()) if t
    )
    import_mean = (
        sum(iv.value for iv in import_series) / len(import_series)
        if import_series
        else None
    )
    known_len = (
        import_series[-1].end - import_series[-1].start if import_series else None
    )
    slot_len = known_len if known_len and known_len > timedelta(0) else DEFAULT_SLOT

    slots: list[PlanSlot] = []
    t = start_utc
    while t < deadline_utc:
        known = _interval_at(import_series, t)
        if known is not None:
            end = known.end
            import_price = known.value
            estimated = False
        else:
            # Align estimated slots to the slot grid (UTC epoch multiples).
            step = slot_len.total_seconds()
            end = datetime.fromtimestamp((t.timestamp() // step + 1) * step, UTC)
            next_known = next((iv.start for iv in import_series if iv.start > t), None)
            if next_known is not None:
                end = min(end, next_known)
            mid = t + (end - t) / 2
            persisted = price_at(import_series, mid - timedelta(days=1))
            if persisted is not None:
                import_price = persisted
            elif current_import_price is not None:
                import_price = current_import_price
            elif import_mean is not None:
                import_price = import_mean
            else:
                import_price = FALLBACK_IMPORT_PRICE
            estimated = bool(import_series)
        end = min(end, deadline_utc)
        mid = t + (end - t) / 2

        export_price = price_at(export_series, mid)
        if export_price is None:
            export_price = price_at(export_series, mid - timedelta(days=1))
        if export_price is None:
            export_price = (
                current_export_price
                if current_export_price is not None
                else FALLBACK_EXPORT_PRICE
            )

        pv_kw = solar_kw_between(samples, t, end)
        slots.append(
            PlanSlot(
                start=t,
                end=end,
                import_price=import_price,
                export_price=export_price,
                surplus_kw=max(0.0, pv_kw - base_load_kw),
                estimated_price=estimated,
            )
        )
        t = end
    return slots


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------


def _slot_segments(
    slot: PlanSlot, min_kw: float, max_kw: float
) -> list[tuple[float, float]]:
    """
    Return the slot's marginal-cost curve as (price_per_kwh, kwh) segments.

    Segments are in the order they must be consumed and have non-decreasing
    prices (lower convex hull), so a global greedy fill is optimal.
    """
    hours = slot.hours
    if hours <= 0 or max_kw <= 0:
        return []
    min_kw = min(max(0.0, min_kw), max_kw)
    solar_kw = min(slot.surplus_kw, max_kw)

    # Marginal cost over the power range [0, max_kw]: solar first, then grid.
    pieces = [
        (w, p)
        for w, p in (
            (solar_kw, slot.export_price),
            (max_kw - solar_kw, slot.import_price),
        )
        if w > 0
    ]

    # Below min_kw the charger can only time-share at min_kw, so [0, min_kw]
    # is a single chord priced at the average cost of running at min_kw.
    merged: list[list[float]] = []
    if min_kw > 0:
        need, cost = min_kw, 0.0
        rest: list[tuple[float, float]] = []
        for w, p in pieces:
            used = min(w, need)
            cost += used * p
            need -= used
            if w - used > 0:
                rest.append((w - used, p))
        merged.append([min_kw, cost / min_kw])
        pieces = rest
    merged.extend([w, p] for w, p in pieces)

    # Pool adjacent violators -> non-decreasing prices (convex hull).
    hull: list[list[float]] = []
    for seg in merged:
        hull.append(seg)
        while len(hull) >= 2 and hull[-1][1] < hull[-2][1]:
            w2, p2 = hull.pop()
            w1, p1 = hull.pop()
            hull.append([w1 + w2, (w1 * p1 + w2 * p2) / (w1 + w2)])

    return [(p, w * hours) for w, p in hull]


@dataclass
class SlotPlan:
    """Planned charging for one slot."""

    slot: PlanSlot
    energy_kwh: float = 0.0
    solar_kwh: float = 0.0
    grid_kwh: float = 0.0
    cost: float = 0.0

    @property
    def avg_power_kw(self) -> float:
        """Return the average charging power over the slot."""
        hours = self.slot.hours
        return self.energy_kwh / hours if hours > 0 else 0.0

    def charge_power_kw(self, min_kw: float, max_kw: float) -> float:
        """
        Power to command now so the slot's energy is delivered.

        Below min_kw the slot is front-loaded at min_kw; the next re-plan
        stops charging once the slot's share has been delivered.
        """
        if self.energy_kwh <= ENERGY_EPS_KWH:
            return 0.0
        return min(max_kw, max(self.avg_power_kw, min_kw))


@dataclass
class ChargePlan:
    """Result of planning a fixed amount of energy over a set of slots."""

    slots: list[SlotPlan]
    requested_kwh: float
    min_kw: float
    max_kw: float
    planned_kwh: float = 0.0
    cost: float = 0.0
    solar_kwh: float = 0.0
    grid_kwh: float = 0.0
    deadline: datetime | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    # Energy given up in this window because the next window is much cheaper
    deferred_kwh: float = 0.0
    deferred_cost: float = 0.0  # what the deferred energy costs in the next window
    avoided_cost: float = 0.0  # what it would have cost in this window

    @property
    def shortfall_kwh(self) -> float:
        """Return the requested energy neither planned nor deferred."""
        return max(0.0, self.requested_kwh - self.planned_kwh - self.deferred_kwh)

    @property
    def current(self) -> SlotPlan | None:
        """Return the plan for the current slot, if any."""
        return self.slots[0] if self.slots else None

    def next_charge(self) -> SlotPlan | None:
        """First slot with planned charging."""
        return next((s for s in self.slots if s.energy_kwh > ENERGY_EPS_KWH), None)


def plan_energy(
    slots: list[PlanSlot],
    energy_kwh: float,
    min_kw: float,
    max_kw: float,
    *,
    next_slots: list[PlanSlot] | None = None,
    next_kwh: float = 0.0,
    min_saving: float = 1.0,
) -> ChargePlan:
    """
    Allocate *energy_kwh* across *slots* at minimum total cost.

    Args:
        slots: Slots of the current window, starting now.
        energy_kwh: Energy still to deliver in the current window.
        min_kw: Minimum charging power.
        max_kw: Maximum charging power.
        next_slots: Slots of the next window with published prices.  When
            given, energy may be deferred to them.
        next_kwh: The next window's own amount, reserved before deferring.
        min_saving: Fraction (0-1) the next window must be cheaper per kWh
            for energy to be deferred; 1 or more disables deferral.

    """
    if next_slots and min_saving < 1:
        return _plan_with_deferral(
            slots, energy_kwh, min_kw, max_kw, next_slots, next_kwh, min_saving
        )

    plan = ChargePlan(
        slots=[SlotPlan(slot=s) for s in slots],
        requested_kwh=max(0.0, energy_kwh),
        min_kw=min_kw,
        max_kw=max_kw,
        deadline=slots[-1].end if slots else None,
    )
    if plan.requested_kwh <= ENERGY_EPS_KWH:
        return plan

    # (price, start, segment index, slot index, kwh); ties go to the earlier slot.
    segments = [
        (price, slot.start, k, i, kwh)
        for i, slot in enumerate(slots)
        for k, (price, kwh) in enumerate(_slot_segments(slot, min_kw, max_kw))
    ]
    segments.sort(key=lambda s: s[:3])

    remaining = plan.requested_kwh
    for _, _, _, i, kwh in segments:
        if remaining <= 0:
            break
        take = min(kwh, remaining)
        plan.slots[i].energy_kwh += take
        remaining -= take

    _finalize(plan)
    return plan


def _net_price_at_power(slot: PlanSlot, power_kw: float) -> float:
    """Return the solar/grid blended price at a selected charging power."""
    if power_kw <= 0:
        return float("inf")
    solar_kw = min(max(0.0, slot.surplus_kw), power_kw)
    return (
        solar_kw * slot.export_price + (power_kw - solar_kw) * slot.import_price
    ) / power_kw


def add_energy_at_or_below_net_price(
    plan: ChargePlan, energy_kwh: float, price_threshold: float
) -> float:
    """
    Add energy where blended net price stays at or below the threshold.

    Each slot is first evaluated at its existing planned charging power, or
    minimum power when no charge is planned. Added energy may raise that power
    only as far as the net price remains within the threshold.
    """
    if energy_kwh <= ENERGY_EPS_KWH:
        return 0.0

    candidates = []
    for slot_index, slot_plan in enumerate(plan.slots):
        if slot_plan.slot.estimated_price:
            continue
        slot = slot_plan.slot
        if slot.hours <= 0 or plan.max_kw <= 0:
            continue

        selected_kw = max(plan.min_kw, slot_plan.avg_power_kw)
        if selected_kw <= 0:
            selected_kw = plan.max_kw
        selected_kw = min(selected_kw, plan.max_kw)
        selected_price = _net_price_at_power(slot, selected_kw)
        if not selected_price <= price_threshold:
            continue

        max_price = _net_price_at_power(slot, plan.max_kw)
        if max_price <= price_threshold:
            qualifying_kw = plan.max_kw
        elif slot.export_price > slot.import_price:
            qualifying_kw = selected_kw
        else:
            low_kw, high_kw = selected_kw, plan.max_kw
            for _ in range(32):
                mid_kw = (low_kw + high_kw) / 2
                if _net_price_at_power(slot, mid_kw) <= price_threshold:
                    low_kw = mid_kw
                else:
                    high_kw = mid_kw
            qualifying_kw = low_kw

        available = max(
            0.0,
            qualifying_kw * slot.hours - slot_plan.energy_kwh,
        )
        if available > ENERGY_EPS_KWH:
            candidates.append((selected_price, slot.start, slot_index, available))

    candidates.sort(key=lambda item: item[:2])
    remaining_kwh = energy_kwh
    additions: dict[int, float] = {}
    for _, _, slot_index, available in candidates:
        if remaining_kwh <= ENERGY_EPS_KWH:
            break
        added = min(remaining_kwh, available)
        additions[slot_index] = additions.get(slot_index, 0.0) + added
        remaining_kwh -= added

    added_total = energy_kwh - remaining_kwh
    if added_total <= ENERGY_EPS_KWH:
        return 0.0

    for slot_index, added in additions.items():
        plan.slots[slot_index].energy_kwh += added
    plan.requested_kwh += added_total
    _finalize(plan)
    return added_total


def _plan_with_deferral(
    slots: list[PlanSlot],
    energy_kwh: float,
    min_kw: float,
    max_kw: float,
    next_slots: list[PlanSlot],
    next_kwh: float,
    min_saving: float,
) -> ChargePlan:
    """Plan the current window, deferring kWh the next window does much cheaper."""
    plan = plan_energy(slots, energy_kwh, min_kw, max_kw)
    if plan.planned_kwh <= ENERGY_EPS_KWH:
        return plan

    # Capacity left in the next window after reserving its own amount.  Each
    # slot consumes its segments in order, so the residual is the tail.
    reserved = plan_energy(next_slots, next_kwh, min_kw, max_kw)
    spare: list[tuple[float, float]] = []  # (price, kwh)
    for sp in reserved.slots:
        used = sp.energy_kwh
        for price, kwh in _slot_segments(sp.slot, min_kw, max_kw):
            skip = min(used, kwh)
            used -= skip
            if kwh - skip > 0:
                spare.append((price, kwh - skip))
    if not spare:
        return plan

    # (key, prefer-today, start, segment, slot index or -1, kwh, real price)
    items = [
        (price, 0, slot.start, k, i, kwh, price)
        for i, slot in enumerate(slots)
        for k, (price, kwh) in enumerate(_slot_segments(slot, min_kw, max_kw))
    ]
    hurdle = 1.0 - min_saving
    items += [
        (price / hurdle, 1, plan.deadline, 0, -1, kwh, price) for price, kwh in spare
    ]
    items.sort(key=lambda it: it[:4])

    deferred = ChargePlan(
        slots=[SlotPlan(slot=s) for s in slots],
        requested_kwh=plan.requested_kwh,
        min_kw=min_kw,
        max_kw=max_kw,
        deadline=plan.deadline,
    )
    # Only what fits in this window competes; a shortfall is never deferred.
    remaining = plan.planned_kwh
    for _, _, _, _, i, kwh, price in items:
        if remaining <= 0:
            break
        take = min(kwh, remaining)
        remaining -= take
        if i >= 0:
            deferred.slots[i].energy_kwh += take
        else:
            deferred.deferred_kwh += take
            deferred.deferred_cost += take * price

    if deferred.deferred_kwh <= ENERGY_EPS_KWH:
        return plan
    _finalize(deferred)
    deferred.avoided_cost = plan.cost - deferred.cost
    return deferred


def _finalize(plan: ChargePlan) -> None:
    """Fill in per-slot solar/grid split, costs, totals and display blocks."""
    min_kw, max_kw = plan.min_kw, plan.max_kw
    plan.planned_kwh = 0.0
    plan.cost = 0.0
    plan.solar_kwh = 0.0
    plan.grid_kwh = 0.0
    for sp in plan.slots:
        if sp.energy_kwh <= 0:
            continue
        hours = sp.slot.hours
        avg_kw = sp.avg_power_kw
        run_kw = min(max_kw, max(avg_kw, min_kw)) if avg_kw > 0 else 0.0
        duty = avg_kw / run_kw if run_kw > 0 else 0.0
        sp.solar_kwh = duty * min(run_kw, sp.slot.surplus_kw) * hours
        sp.grid_kwh = max(0.0, sp.energy_kwh - sp.solar_kwh)
        sp.cost = (
            sp.solar_kwh * sp.slot.export_price + sp.grid_kwh * sp.slot.import_price
        )
        plan.planned_kwh += sp.energy_kwh
        plan.solar_kwh += sp.solar_kwh
        plan.grid_kwh += sp.grid_kwh
        plan.cost += sp.cost

    plan.blocks = _merge_blocks(plan.slots)


def _merge_blocks(slot_plans: list[SlotPlan]) -> list[dict[str, Any]]:
    """Merge consecutive charging slots into blocks for display."""
    blocks: list[dict[str, Any]] = []
    for sp in slot_plans:
        if sp.energy_kwh <= ENERGY_EPS_KWH:
            continue
        last = blocks[-1] if blocks else None
        if last is not None and last["end"] == sp.slot.start:
            last["end"] = sp.slot.end
            last["kwh"] += sp.energy_kwh
            last["solar_kwh"] += sp.solar_kwh
            last["grid_kwh"] += sp.grid_kwh
            last["cost"] += sp.cost
        else:
            blocks.append(
                {
                    "start": sp.slot.start,
                    "end": sp.slot.end,
                    "kwh": sp.energy_kwh,
                    "solar_kwh": sp.solar_kwh,
                    "grid_kwh": sp.grid_kwh,
                    "cost": sp.cost,
                }
            )
    return blocks


# ----------------------------------------------------------------------
# Presentation
# ----------------------------------------------------------------------


def _hhmm(dt: datetime, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def plan_summary(plan: ChargePlan, tz: tzinfo, max_len: int = 255) -> str:
    """Short human-readable plan, suitable for a sensor state (<= 255 chars)."""
    if plan.requested_kwh <= ENERGY_EPS_KWH:
        return "Daily amount reached"
    deferral = (
        f"{plan.deferred_kwh:.1f} kWh deferred to tomorrow "
        f"(est. {plan.deferred_cost:.2f} instead of {plan.avoided_cost:.2f})"
        if plan.deferred_kwh > ENERGY_EPS_KWH
        else ""
    )
    if not plan.blocks:
        return deferral or "No charging possible before deadline"
    parts = []
    for b in plan.blocks:
        solar_pct = round(100 * b["solar_kwh"] / b["kwh"]) if b["kwh"] > 0 else 0
        src = f", {solar_pct}% solar" if solar_pct else ""
        parts.append(
            f"{_hhmm(b['start'], tz)}–{_hhmm(b['end'], tz)} {b['kwh']:.1f} kWh{src}"
        )
    tail = f" · est. cost {plan.cost:.2f}"
    if deferral:
        tail += f" · {deferral}"
    if plan.shortfall_kwh > ENERGY_EPS_KWH:
        tail += f" · short {plan.shortfall_kwh:.1f} kWh"
    text = ", ".join(parts)
    if len(text) + len(tail) > max_len:
        text = text[: max_len - len(tail) - 1] + "…"
    return text + tail


def plan_attributes(plan: ChargePlan, tz: tzinfo) -> dict[str, Any]:
    """Detailed plan for sensor attributes."""

    def iso(dt: datetime) -> str:
        return dt.astimezone(tz).isoformat()

    return {
        "deadline": iso(plan.deadline) if plan.deadline else None,
        "requested_kwh": round(plan.requested_kwh, 2),
        "planned_kwh": round(plan.planned_kwh, 2),
        "shortfall_kwh": round(plan.shortfall_kwh, 2),
        "deferred_kwh": round(plan.deferred_kwh, 2),
        "deferred_cost": round(plan.deferred_cost, 2),
        "deferred_avoided_cost": round(plan.avoided_cost, 2),
        "planned_solar_kwh": round(plan.solar_kwh, 2),
        "planned_grid_kwh": round(plan.grid_kwh, 2),
        "estimated_cost": round(plan.cost, 2),
        "blocks": [
            {
                "start": iso(b["start"]),
                "end": iso(b["end"]),
                "kwh": round(b["kwh"], 2),
                "solar_kwh": round(b["solar_kwh"], 2),
                "grid_kwh": round(b["grid_kwh"], 2),
                "cost": round(b["cost"], 2),
            }
            for b in plan.blocks
        ],
        "schedule": [
            {
                "start": iso(sp.slot.start),
                "kw": round(sp.avg_power_kw, 2),
                "surplus_kw": round(sp.slot.surplus_kw, 2),
                "import_price": round(sp.slot.import_price, 4),
                "export_price": round(sp.slot.export_price, 4),
                **({"estimated_price": True} if sp.slot.estimated_price else {}),
            }
            for sp in plan.slots
        ],
    }


def plan_slots_raw(
    plan: ChargePlan | None, tz: tzinfo, watts_per_amp: float
) -> list[dict[str, Any]]:
    """
    Per-slot plan in Nordpool ``raw_*`` style, for charting.

    ``solar_kwh`` is drawn from the forecast solar surplus (PV minus base load)
    and ``import_kwh`` from the grid, so the slot's cost is
    ``solar_kwh * export_price + import_kwh * import_price``.  ``current`` is
    the current commanded during the slot; slots needing less than the
    minimum power run at the minimum current for part of the slot.  Prices
    extrapolated from the previous day are flagged ``estimated_price``.
    """
    if plan is None or watts_per_amp <= 0:
        return []
    return [
        {
            "start": sp.slot.start.astimezone(tz).isoformat(),
            "end": sp.slot.end.astimezone(tz).isoformat(),
            "kwh": round(sp.energy_kwh, 3),
            "solar_kwh": round(sp.solar_kwh, 3),
            "import_kwh": round(sp.grid_kwh, 3),
            "current": round(
                sp.charge_power_kw(plan.min_kw, plan.max_kw) * 1000 / watts_per_amp
            ),
            "import_price": round(sp.slot.import_price, 4),
            "export_price": round(sp.slot.export_price, 4),
            **({"estimated_price": True} if sp.slot.estimated_price else {}),
        }
        for sp in plan.slots
    ]


def charge_reason_plan_attributes(
    plan: ChargePlan,
    next_plan: ChargePlan | None,
    tz: tzinfo,
    watts_per_amp: float,
    carried_over_kwh: float,
) -> dict[str, Any]:
    """Return detailed current and next-window plan attributes for the reason sensor."""
    return {
        **plan_attributes(plan, tz),
        "carried_over_kwh": round(carried_over_kwh, 2),
        "raw_today": plan_slots_raw(plan, tz, watts_per_amp),
        "raw_tomorrow": plan_slots_raw(next_plan, tz, watts_per_amp),
        "tomorrow_valid": next_plan is not None,
    }


# ----------------------------------------------------------------------
# Delivered-energy tracking
# ----------------------------------------------------------------------

# Longest gap over which the applied-current estimate is integrated.
MAX_ESTIMATE_GAP = timedelta(minutes=15)


@dataclass
class DailyEnergyTracker:
    """
    Track energy delivered to the EV within the current daily window.

    Uses a charger energy meter when available (handles counter resets),
    otherwise integrates the commanded charging power over time.
    """

    window_start: datetime | None = None
    delivered_kwh: float = 0.0
    # Energy deferred by the previous window, added to this window's amount
    carry_over_kwh: float = 0.0
    # Energy the current plan defers to the next window
    deferred_kwh: float = 0.0
    last_meter_kwh: float | None = None
    last_update: datetime | None = None
    last_power_kw: float = 0.0

    def update(
        self, now: datetime, window_start: datetime, meter_kwh: float | None
    ) -> None:
        """Advance the window and add energy delivered since the last update."""
        now_utc = to_utc(now)
        window_utc = to_utc(window_start)
        if self.window_start != window_utc:
            self.window_start = window_utc
            self.delivered_kwh = 0.0
            self.carry_over_kwh = self.deferred_kwh
            self.deferred_kwh = 0.0

        if meter_kwh is not None:
            if self.last_meter_kwh is not None:
                delta = meter_kwh - self.last_meter_kwh
                # A drop means the counter was reset (e.g. a new session meter).
                self.delivered_kwh += delta if delta >= 0 else meter_kwh
            self.last_meter_kwh = meter_kwh
        elif self.last_update is not None and now_utc is not None:
            elapsed = now_utc - self.last_update
            if timedelta(0) < elapsed <= MAX_ESTIMATE_GAP:
                hours = elapsed.total_seconds() / 3600
                self.delivered_kwh += self.last_power_kw * hours
        self.last_update = now_utc

    def as_dict(self) -> dict[str, Any]:
        """Serialize the tracker for storage."""
        return {
            "window_start": (
                self.window_start.isoformat() if self.window_start else None
            ),
            "delivered_kwh": self.delivered_kwh,
            "carry_over_kwh": self.carry_over_kwh,
            "deferred_kwh": self.deferred_kwh,
            "last_meter_kwh": self.last_meter_kwh,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "last_power_kw": self.last_power_kw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyEnergyTracker:
        """Restore a tracker from stored data."""
        return cls(
            window_start=to_utc(data.get("window_start")),
            delivered_kwh=float(data.get("delivered_kwh") or 0.0),
            carry_over_kwh=float(data.get("carry_over_kwh") or 0.0),
            deferred_kwh=float(data.get("deferred_kwh") or 0.0),
            last_meter_kwh=(
                float(data["last_meter_kwh"])
                if data.get("last_meter_kwh") is not None
                else None
            ),
            last_update=to_utc(data.get("last_update")),
            last_power_kw=float(data.get("last_power_kw") or 0.0),
        )
