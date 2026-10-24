"""Tests for the Fixed Daily Amount planner and strategy."""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from ev_charger_manager_pure import charge_strategy as S
from ev_charger_manager_pure import planner as P

TZ = ZoneInfo("Europe/Stockholm")
V, PH = 230, 1
MIN_A, MAX_A = 6, 10
MIN_KW, MAX_KW = MIN_A * V * PH / 1000, MAX_A * V * PH / 1000
BASE_KW = 0.5
DAY = datetime(2026, 10, 12, tzinfo=TZ)


def sun(h):
    """Peak 4 kW at 13:00."""
    return max(0.0, 4.0 * (1 - abs(h - 13) / 5))


def weak_sun(h):
    """Peak 1.5 kW -> surplus 1.0 kW, below the 1.38 kW minimum."""
    return max(0.0, 1.5 * (1 - abs(h - 13) / 4))


def cheap_night(h):
    return 0.2 if h < 6 else (0.5 if h < 17 else 1.5)


def pricey_noon(h):
    return 0.1 if h < 6 else (2.0 if 9 <= h < 17 else 1.0)


def make_day(spot_fn, solar_fn, start=DAY, tax=1.0):
    """15-min import (spot + tax) and export (spot) series plus a solar forecast."""
    imp, exp, solar = [], [], {}
    for q in range(96):
        t = start.astimezone(UTC) + timedelta(minutes=15 * q)
        local = t.astimezone(TZ)
        spot = spot_fn(local.hour + local.minute / 60)
        imp.append(P.PriceInterval(t, t + timedelta(minutes=15), spot + tax))
        exp.append(P.PriceInterval(t, t + timedelta(minutes=15), spot))
        solar[t] = solar_fn(local.hour + local.minute / 60)
    return imp, exp, solar


def plan_day(spot_fn, solar_fn, kwh, now_h=0.0):
    imp, exp, solar = make_day(spot_fn, solar_fn)
    slots = P.build_slots(
        DAY + timedelta(hours=now_h), DAY + timedelta(days=1), imp, exp, solar, BASE_KW
    )
    return P.plan_energy(slots, kwh, MIN_KW, MAX_KW)


# ----------------------------------------------------------------------
# Planning scenarios
# ----------------------------------------------------------------------


def test_prefers_solar_when_export_price_below_night_import():
    plan = plan_day(cheap_night, sun, 10)
    assert plan.planned_kwh == pytest.approx(10)
    assert plan.grid_kwh < 0.5


def test_exports_solar_and_imports_at_night_when_cheaper():
    plan = plan_day(pricey_noon, sun, 10)
    assert plan.planned_kwh == pytest.approx(10)
    assert plan.solar_kwh < 0.5
    assert all(b["start"].astimezone(TZ).hour < 6 for b in plan.blocks)


def test_infeasible_amount_reports_shortfall():
    plan = plan_day(cheap_night, sun, 100)
    assert plan.planned_kwh == pytest.approx(MAX_KW * 24)
    assert plan.shortfall_kwh == pytest.approx(100 - MAX_KW * 24)


def test_nothing_requested_stops_charging():
    plan = plan_day(cheap_night, sun, 0)
    decision = S.strategy_fixed_daily_amount(plan, MIN_A, MAX_A, PH, V, TZ)
    assert decision.target_current == 0
    assert P.plan_summary(plan, TZ) == "Daily amount reached"


def test_opportunistic_allowance_is_cumulative_and_soc_capped():
    assert S.opportunistic_kwh_remaining(10, 12, 5, 0, 2) == pytest.approx(2)
    assert S.opportunistic_kwh_remaining(10, 8, 5, 2, 2) == pytest.approx(0)
    assert S.opportunistic_kwh_remaining(10, 8, 5, 2, None) == pytest.approx(5)


def test_fixed_daily_amount_is_capped_by_soc_targets():
    assert S.fixed_daily_kwh_remaining(10, 0, 0, 8, 3) == pytest.approx(3)
    assert S.fixed_daily_kwh_remaining(10, 0, 0, 3, 8) == pytest.approx(3)
    assert S.fixed_daily_kwh_remaining(10, 0, 0, None, None) == pytest.approx(10)
    assert S.fixed_daily_kwh_remaining(10, 0, 0, 8, 0) == pytest.approx(0)
    assert S.fixed_daily_kwh_remaining(10, 2, 3, 8, 6) == pytest.approx(6)


def test_opportunistic_energy_can_fill_above_fixed_daily_target_to_ev_target():
    remaining_base_kwh = S.fixed_daily_kwh_remaining(10, 0, 0, 14, 4)

    assert S.opportunistic_kwh_remaining(
        10, 0, 20, remaining_base_kwh, 14
    ) == pytest.approx(10)


def test_opportunistic_energy_is_scheduled_in_future_qualifying_slots():
    slots = [
        P.PlanSlot(DAY, DAY + timedelta(hours=1), 1.5, 0.5, 0.0),
        P.PlanSlot(DAY + timedelta(hours=1), DAY + timedelta(hours=2), 1.04, 1.04, 0.0),
        P.PlanSlot(DAY + timedelta(hours=2), DAY + timedelta(hours=3), 1.3, 0.5, 0.0),
        P.PlanSlot(DAY + timedelta(hours=3), DAY + timedelta(hours=4), 1.21, 0.4, 0.0),
    ]
    plan = P.plan_energy(slots, 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.04, 2.0)

    assert added == pytest.approx(2.0)
    assert plan.slots[0].energy_kwh == pytest.approx(0.0)
    assert plan.slots[1].energy_kwh == pytest.approx(2.0)
    assert plan.slots[2].energy_kwh == pytest.approx(0.0)
    assert plan.slots[3].energy_kwh == pytest.approx(0.0)
    assert plan.requested_kwh == pytest.approx(2.0)
    assert plan.planned_kwh == pytest.approx(2.0)


def test_opportunistic_energy_prioritizes_solar_below_price_threshold():
    slots = [
        P.PlanSlot(DAY, DAY + timedelta(hours=1), 1.04, 1.04, 0.0),
        P.PlanSlot(
            DAY + timedelta(hours=1),
            DAY + timedelta(hours=2),
            2.0,
            0.4,
            MAX_KW,
        ),
    ]
    plan = P.plan_energy(slots, 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 2.0)

    assert added == pytest.approx(2.0)
    assert plan.slots[0].energy_kwh == pytest.approx(0.0)
    assert plan.slots[1].energy_kwh == pytest.approx(2.0)
    assert plan.slots[1].solar_kwh == pytest.approx(2.0)


def test_opportunistic_energy_uses_net_price_at_selected_current():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 2.0, 1.0, 1.5)
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.5, 2.0)

    assert added == pytest.approx(2.0)
    assert plan.current.avg_power_kw == pytest.approx(2.0)
    assert plan.current.cost / plan.current.energy_kwh <= 1.5


def test_opportunistic_net_price_caps_charge_power_at_threshold():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 2.0, 1.0, 1.5)
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 10.0)

    assert added == pytest.approx(1.875)
    assert plan.current.cost / plan.current.energy_kwh == pytest.approx(1.2)


def test_opportunistic_net_price_uses_existing_planned_current():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 2.0, 1.0, 1.5)
    plan = P.plan_energy([slot], 2.1, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 1.0)

    assert added == 0


def test_opportunistic_energy_uses_only_published_qualifying_price_slots():
    slots = [
        P.PlanSlot(
            DAY,
            DAY + timedelta(hours=1),
            1.04,
            1.04,
            0.0,
            estimated_price=True,
        ),
        P.PlanSlot(DAY + timedelta(hours=1), DAY + timedelta(hours=2), 1.04, 1.04, 0.0),
    ]
    plan = P.plan_energy(slots, 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 2.0)

    assert added == pytest.approx(2.0)
    assert plan.slots[0].energy_kwh == pytest.approx(0.0)
    assert plan.slots[1].energy_kwh == pytest.approx(2.0)


def test_opportunistic_energy_respects_remaining_slot_capacity_and_allowance():
    slot = P.PlanSlot(DAY, DAY + timedelta(minutes=15), 1.04, 1.04, 0.0)
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 10.0)

    assert added == pytest.approx(MAX_KW / 4)
    assert plan.current.energy_kwh == pytest.approx(MAX_KW / 4)
    assert plan.requested_kwh == pytest.approx(MAX_KW / 4)


def test_opportunistic_energy_threshold_excludes_above_threshold_prices():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 1.21, 1.21, 0.0)
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 2.0)

    assert added == 0
    assert plan.planned_kwh == 0


def test_opportunistic_energy_only_uses_finite_slot_capacity():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 1.04, 1.04, 0.0)
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    added = S.add_opportunistic_energy(plan, 1.2, 100.0)

    assert added == pytest.approx(MAX_KW)
    assert plan.requested_kwh == pytest.approx(MAX_KW)
    assert plan.planned_kwh == pytest.approx(MAX_KW)


def test_opportunistic_planning_keeps_unqualifying_slots_off():
    slot = P.PlanSlot(
        DAY,
        DAY + timedelta(hours=1),
        2.0,
        0.4,
        0.0,
    )
    plan = P.plan_energy([slot], 0, MIN_KW, MAX_KW)

    assert S.add_opportunistic_energy(plan, 1.2, 2.0) == 0


def test_waits_until_planned_slot():
    plan = plan_day(cheap_night, sun, 10)
    decision = S.strategy_fixed_daily_amount(plan, MIN_A, MAX_A, PH, V, TZ)
    assert decision.target_current == 0
    assert "next charge 10:30" in decision.reason


def test_partial_slot_is_front_loaded_at_min_current():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    slot = P.PlanSlot(t0, t0 + timedelta(hours=1), 1.0, 0.1, 0.0)
    plan = P.plan_energy([slot], 0.5, MIN_KW, MAX_KW)
    decision = S.strategy_fixed_daily_amount(plan, MIN_A, MAX_A, PH, V, TZ)
    assert decision.target_current == MIN_A


def _hull_cost(slot, e):
    """Brute-force cost of e kWh in a slot, running at power q for part of it."""
    h = slot.hours
    qs = [MIN_KW + (MAX_KW - MIN_KW) * i / 200 for i in range(201)]
    qs += [x for x in (e / h, slot.surplus_kw) if MIN_KW <= x <= MAX_KW]
    best = float("inf")
    for q in qs:
        duty = e / (q * h)
        if duty > 1 + 1e-9:
            continue
        s = min(q, slot.surplus_kw)
        best = min(
            best, duty * h * (s * slot.export_price + (q - s) * slot.import_price)
        )
    return best


def test_greedy_plan_is_optimal_against_random_allocations():
    rng = random.Random(1)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for _ in range(100):
        n = rng.randint(1, 6)
        slots = []
        for i in range(n):
            imp = rng.uniform(0.5, 3)
            slots.append(
                P.PlanSlot(
                    t0 + timedelta(hours=i),
                    t0 + timedelta(hours=i + 1),
                    imp,
                    rng.uniform(0, imp),
                    rng.choice([0.0, rng.uniform(0, 3)]),
                )
            )
        energy = rng.uniform(0, MAX_KW * n)
        plan = P.plan_energy(slots, energy, MIN_KW, MAX_KW)
        assert plan.cost == pytest.approx(
            sum(_hull_cost(sp.slot, sp.energy_kwh) for sp in plan.slots), abs=1e-3
        )
        for _ in range(100):
            w = [rng.random() ** 3 for _ in slots]
            alloc = [min(MAX_KW, energy * x / sum(w)) for x in w]
            short = energy - sum(alloc)
            for i in range(n):
                add = min(MAX_KW - alloc[i], short)
                alloc[i] += add
                short -= add
            other = sum(_hull_cost(s, a) for s, a in zip(slots, alloc, strict=True))
            assert plan.cost <= other + 1e-6


# ----------------------------------------------------------------------
# Closed loop: re-plan every 5 minutes and apply whole amperes
# ----------------------------------------------------------------------


def simulate(spot_fn, solar_fn, kwh, forecast_scale=1.0):
    imp, exp, solar = make_day(spot_fn, solar_fn)
    forecast = {t: v * forecast_scale for t, v in solar.items()}
    actual = sorted(solar.items())
    delivered = cost = 0.0
    t = DAY
    while t < DAY + timedelta(days=1):
        slots = P.build_slots(t, DAY + timedelta(days=1), imp, exp, forecast, BASE_KW)
        surplus = max(
            0.0, P.solar_kw_between(actual, slots[0].start, slots[0].end) - BASE_KW
        )
        slots[0] = replace(slots[0], surplus_kw=surplus)
        plan = P.plan_energy(slots, max(0.0, kwh - delivered), MIN_KW, MAX_KW)
        decision = S.strategy_fixed_daily_amount(plan, MIN_A, MAX_A, PH, V, TZ)
        kw = round(decision.target_current) * V * PH / 1000
        s = min(kw, surplus)
        cost += (s * slots[0].export_price + (kw - s) * slots[0].import_price) * 5 / 60
        delivered += kw * 5 / 60
        t += timedelta(minutes=5)
    return delivered, cost


@pytest.mark.parametrize(
    ("spot_fn", "solar_fn", "forecast_scale"),
    [
        (cheap_night, sun, 1.0),
        (cheap_night, sun, 1.5),
        (pricey_noon, sun, 1.0),
        (cheap_night, weak_sun, 1.0),
    ],
)
def test_closed_loop_delivers_amount_near_plan_cost(spot_fn, solar_fn, forecast_scale):
    ideal = plan_day(spot_fn, solar_fn, 10)
    delivered, cost = simulate(spot_fn, solar_fn, 10, forecast_scale)
    assert delivered == pytest.approx(10, abs=0.3)
    assert cost == pytest.approx(ideal.cost, rel=0.02)


# ----------------------------------------------------------------------
# Input parsing
# ----------------------------------------------------------------------

DST_DAY = datetime(2026, 10, 25, tzinfo=TZ)  # 25 h day
DST_NEXT = datetime(2026, 10, 26, tzinfo=TZ)
DST_AFTER = datetime(2026, 10, 27, tzinfo=TZ)


def test_plain_price_lists_follow_dst_day_length():
    series = P.parse_price_series(
        {"today": list(range(100)), "tomorrow": [1.0] * 96},
        DST_DAY,
        DST_NEXT,
        DST_AFTER,
    )
    assert len(series) == 196
    assert series[99].end == DST_NEXT.astimezone(UTC)
    assert series[100].start == DST_NEXT.astimezone(UTC)


def test_raw_price_lists_are_preferred():
    start = DST_DAY.astimezone(UTC)
    raw = [
        {
            "start": (start + timedelta(hours=i)).isoformat(),
            "end": (start + timedelta(hours=i + 1)).isoformat(),
            "value": i,
        }
        for i in range(25)
    ]
    series = P.parse_price_series(
        {"raw_today": raw, "raw_tomorrow": [], "today": [0] * 25},
        DST_DAY,
        DST_NEXT,
        DST_AFTER,
    )
    assert len(series) == 25
    assert series[3].value == 3


def test_missing_values_keep_alignment():
    series = P.parse_price_series(
        {"today": [None] * 4 + [1.0] * 92, "tomorrow": None},
        DAY,
        DAY + timedelta(days=1),
        DAY + timedelta(days=2),
    )
    assert len(series) == 92
    assert series[0].start == (DAY + timedelta(hours=1)).astimezone(UTC)


def test_timestamp_price_mapping_spans_both_days():
    prices = {
        (DAY + timedelta(minutes=15 * q)).isoformat(sep=" "): float(q)
        for q in range(192)
    }
    series = P.parse_price_series(
        {"prices": prices}, DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)
    )
    assert len(series) == 192
    assert series[100].start == (DAY + timedelta(minutes=1500)).astimezone(UTC)
    assert series[-1].end == (DAY + timedelta(days=2)).astimezone(UTC)
    assert series[-1].value == 191


def test_timestamp_price_mapping_avoids_expensive_current_slot():
    # All-in evening prices from a "prices: {start: value}" attribute
    evening = [2.899, 2.930, 3.265, 3.101, 3.014, 2.809, 2.953, 2.910, 2.843]
    evening += [2.659, 2.910, 2.718, 2.713, 2.685, 2.766, 2.598, 2.509, 2.230]
    evening += [2.391, 2.134, 2.045, 1.876, 2.094, 2.016, 1.906, 1.815]
    start = DAY - timedelta(days=1) + timedelta(hours=17, minutes=30)
    prices = {
        (start + timedelta(minutes=15 * q)).isoformat(sep=" "): p
        for q, p in enumerate(evening)
    }
    series = P.parse_price_series(
        {"prices": prices}, DAY - timedelta(days=1), DAY, DAY + timedelta(days=1)
    )
    slots = P.build_slots(start, DAY, series, [], {}, BASE_KW, 2.899)
    plan = P.plan_energy(slots, 1.0, MIN_KW, MAX_KW)
    assert plan.current.energy_kwh == 0
    charged = {s.slot.import_price for s in plan.slots if s.energy_kwh > 0}
    assert max(charged) < 2.0


def test_unpublished_slots_use_previous_day_prices():
    imp, exp, solar = make_day(cheap_night, sun)
    slots = P.build_slots(
        DAY + timedelta(hours=20),
        DAY + timedelta(days=1, hours=7),
        imp,
        exp,
        solar,
        BASE_KW,
    )
    estimated = [s for s in slots if s.estimated_price]
    assert len(estimated) == 28
    assert estimated[8].import_price == pytest.approx(1.2)


# ----------------------------------------------------------------------
# Delivered-energy tracking
# ----------------------------------------------------------------------


def test_tracker_meter_handles_counter_reset_and_window_rollover():
    tr = P.DailyEnergyTracker()
    tr.update(DAY + timedelta(hours=1), DAY, 100.0)
    tr.update(DAY + timedelta(hours=2), DAY, 102.5)
    tr.update(DAY + timedelta(hours=3), DAY, 1.0)  # session counter reset
    assert tr.delivered_kwh == pytest.approx(3.5)
    assert P.DailyEnergyTracker.from_dict(tr.as_dict()) == tr
    tomorrow = DAY + timedelta(days=1)
    tr.update(tomorrow + timedelta(minutes=1), tomorrow, 1.5)
    assert tr.delivered_kwh == pytest.approx(0.5)


def test_tracker_integrates_power_without_meter():
    tr = P.DailyEnergyTracker()
    tr.update(DAY, DAY, None)
    tr.last_power_kw = 2.3
    tr.update(DAY + timedelta(minutes=5), DAY, None)
    assert tr.delivered_kwh == pytest.approx(2.3 * 5 / 60)
    tr.update(DAY + timedelta(hours=2), DAY, None)  # gap too long, ignored
    assert tr.delivered_kwh == pytest.approx(2.3 * 5 / 60)


# ----------------------------------------------------------------------
# Deferring to the next window once its prices are published
# ----------------------------------------------------------------------

T14 = datetime(2026, 10, 12, 14, tzinfo=TZ).astimezone(UTC)
MIDNIGHT = datetime(2026, 10, 13, tzinfo=TZ).astimezone(UTC)


def hourly(start, prices, surplus=None):
    surplus = surplus or [0.0] * len(prices)
    return [
        P.PlanSlot(
            start + timedelta(hours=i), start + timedelta(hours=i + 1), p, 0.3, s
        )
        for i, (p, s) in enumerate(zip(prices, surplus, strict=True))
    ]


TODAY_EXPENSIVE = hourly(T14, [2.0] * 10)
TOMORROW_CHEAP_NIGHT = hourly(MIDNIGHT, [0.8] * 6 + [1.5] * 18)


def test_defers_to_tomorrow_when_at_least_min_saving_cheaper():
    plan = P.plan_energy(
        TODAY_EXPENSIVE,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=0.5,
    )
    assert plan.deferred_kwh == pytest.approx(5)
    assert plan.planned_kwh == pytest.approx(0)
    assert plan.shortfall_kwh == pytest.approx(0)
    assert plan.deferred_cost == pytest.approx(5 * 0.8)
    assert plan.avoided_cost == pytest.approx(5 * 2.0)
    decision = S.strategy_fixed_daily_amount(plan, MIN_A, MAX_A, PH, V, TZ)
    assert decision.target_current == 0
    assert "5.0 kWh deferred to tomorrow" in decision.reason
    assert P.plan_summary(plan, TZ).startswith("5.0 kWh deferred to tomorrow")


def test_keeps_today_when_saving_below_threshold():
    plan = P.plan_energy(
        TODAY_EXPENSIVE,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=0.7,
    )
    assert plan.deferred_kwh == 0
    assert plan.planned_kwh == pytest.approx(5)


def test_deferral_disabled_at_full_saving():
    plan = P.plan_energy(
        TODAY_EXPENSIVE,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=1.0,
    )
    assert plan.deferred_kwh == 0


def test_only_expensive_part_of_today_is_deferred():
    today = hourly(T14, [2.0] * 10, surplus=[MAX_KW] + [0.0] * 9)  # 1 h of solar
    plan = P.plan_energy(
        today,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=0.5,
    )
    assert plan.planned_kwh == pytest.approx(MAX_KW)
    assert plan.solar_kwh == pytest.approx(MAX_KW)
    assert plan.deferred_kwh == pytest.approx(5 - MAX_KW)


def test_tomorrows_own_amount_is_reserved_first():
    tomorrow = hourly(MIDNIGHT, [0.8] * 2 + [1.5] * 22)  # cheap hours fit only 4.6 kWh
    plan = P.plan_energy(
        TODAY_EXPENSIVE,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=tomorrow,
        next_kwh=2 * MAX_KW,
        min_saving=0.5,
    )
    assert plan.deferred_kwh == 0


def test_shortfall_is_not_deferred():
    today = hourly(T14, [2.0])  # only 2.3 kWh fits today
    plan = P.plan_energy(
        today,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=0.5,
    )
    assert plan.deferred_kwh == pytest.approx(MAX_KW)
    assert plan.shortfall_kwh == pytest.approx(5 - MAX_KW)


def test_no_next_window_slots_before_prices_are_published():
    imp, exp, solar = make_day(cheap_night, sun)  # only today's prices known
    next_window = P.build_slots(
        DAY + timedelta(days=1), DAY + timedelta(days=2), imp, exp, solar, BASE_KW
    )
    assert next_window
    assert all(s.estimated_price for s in next_window)


def test_tracker_carries_deferred_energy_into_next_window():
    tr = P.DailyEnergyTracker()
    tr.update(DAY + timedelta(hours=14), DAY, 10.0)
    tr.deferred_kwh = 4.0
    assert P.DailyEnergyTracker.from_dict(tr.as_dict()) == tr
    tomorrow = DAY + timedelta(days=1)
    tr.update(tomorrow, tomorrow, 10.0)
    assert tr.carry_over_kwh == pytest.approx(4.0)
    assert tr.deferred_kwh == 0
    day_after = tomorrow + timedelta(days=1)
    tr.update(day_after, day_after, 10.0)
    assert tr.carry_over_kwh == 0


def test_reason_describes_today_and_tomorrow_plans():
    today = hourly(T14, [2.0] * 8 + [0.5, 1.0])
    plan = P.plan_energy(today, 3, MIN_KW, MAX_KW)
    tomorrow = P.plan_energy(TOMORROW_CHEAP_NIGHT[:12], 5, MIN_KW, MAX_KW)
    decision = S.strategy_fixed_daily_amount(
        plan,
        MIN_A,
        MAX_A,
        PH,
        V,
        TZ,
        next_plan=tomorrow,
        next_window_end=MIDNIGHT + timedelta(days=1),
    )
    assert decision.target_current == 0
    assert decision.reason == (
        "Fixed daily: waiting, next charge 22:00. "
        "Today: 3.0 kWh 22:00–00:00, est. 1.85. "
        "Tomorrow: 5.0 kWh 00:00–04:00, est. 4.00, prices until 12:00"
    )


def test_reason_says_when_tomorrows_prices_are_missing():
    plan = P.plan_energy(TODAY_EXPENSIVE, 0, MIN_KW, MAX_KW)
    decision = S.strategy_fixed_daily_amount(
        plan, MIN_A, MAX_A, PH, V, TZ, next_window_end=MIDNIGHT + timedelta(days=1)
    )
    assert decision.reason == (
        "Fixed daily: daily amount reached. Tomorrow: prices not published yet"
    )


def test_reason_reports_deferral_saving():
    plan = P.plan_energy(
        TODAY_EXPENSIVE,
        5,
        MIN_KW,
        MAX_KW,
        next_slots=TOMORROW_CHEAP_NIGHT,
        next_kwh=5,
        min_saving=0.5,
    )
    tomorrow = P.plan_energy(
        TOMORROW_CHEAP_NIGHT, 5 + plan.deferred_kwh, MIN_KW, MAX_KW
    )
    decision = S.strategy_fixed_daily_amount(
        plan,
        MIN_A,
        MAX_A,
        PH,
        V,
        TZ,
        next_plan=tomorrow,
        next_window_end=MIDNIGHT + timedelta(days=1),
    )
    assert decision.reason == (
        "Fixed daily: waiting, no charging planned. "
        "Today: 5.0 kWh deferred to tomorrow, saves 6.00. "
        "Tomorrow: 10.0 kWh (incl. 5.0 deferred) 00:00–06:00, est. 8.00"
    )


def test_raw_slots_report_energy_and_current_per_slot():
    today = hourly(T14, [2.0] * 8 + [0.5, 1.0])
    plan = P.plan_energy(today, 3, MIN_KW, MAX_KW)
    raw = P.plan_slots_raw(plan, TZ, V * PH)
    assert len(raw) == 10
    assert raw[0]["start"] == "2026-10-12T14:00:00+02:00"
    assert raw[0]["end"] == "2026-10-12T15:00:00+02:00"
    assert [r["kwh"] for r in raw[-3:]] == [0, 2.3, 0.7]
    # A slot below minimum power runs at the minimum current for part of it
    assert [r["current"] for r in raw[-3:]] == [0, MAX_A, MIN_A]
    assert [r["import_kwh"] for r in raw[-3:]] == [0, 2.3, 0.7]
    assert raw[-1]["import_price"] == 1.0
    assert raw[-1]["export_price"] == 0.3
    assert "estimated_price" not in raw[-1]
    assert P.plan_slots_raw(None, TZ, V * PH) == []


def test_charge_reason_plan_attributes_include_summary_and_slot_detail():
    slot = P.PlanSlot(DAY, DAY + timedelta(hours=1), 2.0, 1.0, 1.5)
    plan = P.plan_energy([slot], 2.0, MIN_KW, MAX_KW)

    attrs = P.charge_reason_plan_attributes(plan, None, TZ, V * PH, 0.5)

    assert attrs["requested_kwh"] == pytest.approx(2.0)
    assert attrs["planned_kwh"] == pytest.approx(2.0)
    assert attrs["estimated_cost"] == pytest.approx(plan.cost)
    assert attrs["carried_over_kwh"] == pytest.approx(0.5)
    assert attrs["blocks"][0]["kwh"] == pytest.approx(2.0)
    assert attrs["schedule"][0]["kw"] == pytest.approx(2.0)
    assert attrs["raw_today"][0]["kwh"] == pytest.approx(2.0)
    assert attrs["raw_tomorrow"] == []
    assert attrs["tomorrow_valid"] is False


def test_raw_slots_split_solar_surplus_and_import():
    plan = P.plan_energy(hourly(T14, [2.0], [1.0]), MAX_KW, MIN_KW, MAX_KW)
    raw = P.plan_slots_raw(plan, TZ, V * PH)
    assert raw[0]["kwh"] == pytest.approx(MAX_KW)
    assert raw[0]["solar_kwh"] == pytest.approx(1.0)
    assert raw[0]["import_kwh"] == pytest.approx(MAX_KW - 1.0)
