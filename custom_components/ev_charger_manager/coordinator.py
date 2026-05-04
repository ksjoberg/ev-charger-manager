"""DataUpdateCoordinator for EV Charger Manager."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .charge_strategy import (
    ChargeDecision,
    strategy_asap,
    strategy_minimize_cost,
    strategy_solar_excess,
    strategy_solar_price_blend,
)
from .const import (
    CONF_BASE_LOAD_W,
    CONF_CHARGE_DEADBAND,
    CONF_CHARGE_MODE,
    CONF_CHARGER_CURRENT_ENTITY,
    CONF_FORECAST_SOLAR_ENTITIES,
    CONF_GRID_POWER_ENTITY,
    CONF_NORDPOOL_EXPORT_ENTITY,
    CONF_NORDPOOL_IMPORT_ENTITY,
    CONF_PRICE_AWARENESS,
    CONF_PV_POWER_ENTITY,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_EV_BATTERY_CAPACITY_ENTITY,
    CONF_EV_SOC_ENTITY,
    CONF_EV_TARGET_SOC_ENTITY,
    CONF_PHASES,
    CONF_VOLTAGE,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_CHARGE_DEADBAND,
    DEFAULT_CHARGE_HOURS,
    DEFAULT_CHARGE_MODE,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_PRICE_AWARENESS,
    DEFAULT_VOLTAGE,
    DOMAIN,
    LOGGER,
    NORDPOOL_PRICE_ATTRS,
    NORDPOOL_TOMORROW_ATTRS,
    UPDATE_INTERVAL_MINUTES,
    ChargeMode,
)
from homeassistant.util import dt as dt_util

from .data import EVChargerData
from .solar import read_solar_forecast, solar_forecast_at

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EVChargerManagerConfigEntry


class EVChargerManagerCoordinator(DataUpdateCoordinator[EVChargerData]):
    """Coordinator that reads HA entity states and applies a charge strategy."""

    config_entry: EVChargerManagerConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EVChargerManagerConfigEntry) -> None:
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self.config_entry = entry

    # ------------------------------------------------------------------
    # Properties derived from config entry (updated when entry reloads)
    # ------------------------------------------------------------------

    @property
    def charger_entity(self) -> str:
        return self.config_entry.data[CONF_CHARGER_CURRENT_ENTITY]

    @property
    def min_current(self) -> float:
        return float(self.config_entry.options.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT))

    @min_current.setter
    def min_current(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_MIN_CURRENT: value},
        )

    @property
    def max_current(self) -> float:
        return float(self.config_entry.options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT))

    @max_current.setter
    def max_current(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_MAX_CURRENT: value},
        )

    @property
    def price_awareness(self) -> float:
        return float(self.config_entry.options.get(CONF_PRICE_AWARENESS, DEFAULT_PRICE_AWARENESS))

    @price_awareness.setter
    def price_awareness(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_PRICE_AWARENESS: value},
        )

    @property
    def charge_deadband(self) -> float:
        return float(self.config_entry.options.get(CONF_CHARGE_DEADBAND, DEFAULT_CHARGE_DEADBAND))

    @charge_deadband.setter
    def charge_deadband(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_CHARGE_DEADBAND: value},
        )

    @property
    def phases(self) -> int:
        return int(self.config_entry.data.get(CONF_PHASES, DEFAULT_PHASES))

    @property
    def voltage(self) -> float:
        return float(self.config_entry.data.get(CONF_VOLTAGE, DEFAULT_VOLTAGE))

    @property
    def grid_power_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_GRID_POWER_ENTITY)

    @property
    def pv_power_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_PV_POWER_ENTITY)

    @property
    def nordpool_import_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_NORDPOOL_IMPORT_ENTITY)

    @property
    def nordpool_export_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_NORDPOOL_EXPORT_ENTITY)

    @property
    def ev_battery_capacity_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_EV_BATTERY_CAPACITY_ENTITY)

    @property
    def ev_target_soc_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_EV_TARGET_SOC_ENTITY)

    @property
    def ev_soc_entity(self) -> str | None:
        return self.config_entry.data.get(CONF_EV_SOC_ENTITY)

    @property
    def forecast_solar_entities(self) -> list[str]:
        return list(self.config_entry.data.get(CONF_FORECAST_SOLAR_ENTITIES) or [])

    @property
    def base_load_w(self) -> float:
        return float(self.config_entry.options.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))

    @base_load_w.setter
    def base_load_w(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_BASE_LOAD_W: value},
        )

    # ------------------------------------------------------------------
    # Options (changeable at runtime via select/number entities)
    # ------------------------------------------------------------------

    @property
    def current_mode(self) -> ChargeMode:
        return ChargeMode(
            self.config_entry.options.get(CONF_CHARGE_MODE, DEFAULT_CHARGE_MODE)
        )

    @current_mode.setter
    def current_mode(self, mode: ChargeMode) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_CHARGE_MODE: mode.value},
        )

    # ------------------------------------------------------------------
    # Main update logic
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> EVChargerData:
        """Read HA states, compute a charge decision, and apply it."""
        result = EVChargerData(mode=self.current_mode.value)
        now = dt_util.now()

        # --- Forecast.Solar: aggregate hourly forecast from configured entities ---
        result.hourly_solar_forecast = _extract_solar_forecast(
            self.hass, self.forecast_solar_entities, self.base_load_w
        )

        # --- Solar forecast kW for current moment (from Forecast.Solar raw data) ---
        if self.forecast_solar_entities:
            combined_forecast: dict = {}
            for entity_id in self.forecast_solar_entities:
                state = self.hass.states.get(entity_id)
                if state is None or state.state in ("unavailable", "unknown"):
                    continue
                for dt, kw in read_solar_forecast(dict(state.attributes)).items():
                    combined_forecast[dt] = combined_forecast.get(dt, 0.0) + kw
            if combined_forecast:
                raw_kw = solar_forecast_at(combined_forecast, now)
                if raw_kw is not None:
                    base_kw = self.base_load_w / 1000.0
                    result.solar_forecast_kw = round(max(0.0, raw_kw - base_kw), 3)

        # --- Solar power: real PV sensor only (None when no sensor configured) ---
        if self.pv_power_entity:
            pv_state = self.hass.states.get(self.pv_power_entity)
            if pv_state and pv_state.state not in ("unavailable", "unknown"):
                try:
                    raw = float(pv_state.state)
                    unit = pv_state.attributes.get("unit_of_measurement", "")
                    result.solar_power_kw = raw / 1000.0 if unit == "W" else raw
                except ValueError:
                    LOGGER.warning(
                        "Could not parse PV power from %s: %s",
                        self.pv_power_entity,
                        pv_state.state,
                    )

        # effective_solar_kw: actual measurement when available, else forecast
        effective_solar_kw = (
            result.solar_power_kw
            if result.solar_power_kw is not None
            else (result.hourly_solar_forecast[0] if result.hourly_solar_forecast else 0.0)
        )

        # --- Grid power (positive = export available for EV) ---
        if self.grid_power_entity:
            gp_state = self.hass.states.get(self.grid_power_entity)
            if gp_state and gp_state.state not in ("unavailable", "unknown"):
                try:
                    raw = float(gp_state.state)
                    unit = gp_state.attributes.get("unit_of_measurement", "")
                    result.grid_export_kw = raw / 1000.0 if unit == "W" else raw
                except ValueError:
                    LOGGER.warning(
                        "Could not parse grid power from %s: %s",
                        self.grid_power_entity,
                        gp_state.state,
                    )

        # Available solar excess: grid export if measured, else effective solar
        solar_excess_kw = (
            result.grid_export_kw if result.grid_export_kw is not None else effective_solar_kw
        )

        # --- Nordpool import price ---
        slots_per_hour = 1
        today_prices: list[float] = []
        tomorrow_prices: list[float] = []
        future_prices: list[float] = []
        if self.nordpool_import_entity:
            np_state = self.hass.states.get(self.nordpool_import_entity)
            if np_state and np_state.state not in ("unavailable", "unknown"):
                try:
                    result.current_import_price = float(np_state.state)
                except ValueError:
                    LOGGER.warning(
                        "Could not parse import price from %s: %s",
                        self.nordpool_import_entity,
                        np_state.state,
                    )

                attrs = np_state.attributes
                today_prices = _extract_price_list(attrs, NORDPOOL_PRICE_ATTRS)
                tomorrow_prices = _extract_price_list(attrs, NORDPOOL_TOMORROW_ATTRS)
                result.hourly_prices = today_prices + tomorrow_prices
                if len(today_prices) >= 20:
                    slots_per_hour = max(1, round(len(today_prices) / 24))
                current_slot = now.hour * slots_per_hour
                future_prices = result.hourly_prices[current_slot:]

        # --- Nordpool export price ---
        if self.nordpool_export_entity:
            np_state = self.hass.states.get(self.nordpool_export_entity)
            if np_state and np_state.state not in ("unavailable", "unknown"):
                try:
                    result.current_export_price = float(np_state.state)
                except ValueError:
                    LOGGER.warning(
                        "Could not parse export price from %s: %s",
                        self.nordpool_export_entity,
                        np_state.state,
                    )

        # --- EV state of charge / kWh needed ---
        result.ev_kwh_needed = self._compute_ev_kwh_needed()

        # --- Hours needed at max current ---
        charging_power_kw = (self.max_current * self.phases * self.voltage) / 1000.0
        if result.ev_kwh_needed is not None and charging_power_kw > 0:
            result.charge_hours_needed = result.ev_kwh_needed / charging_power_kw

        # --- Determine target current ---
        mode = self.current_mode

        # Stop immediately when EV has reached its target SoC
        if result.ev_kwh_needed is not None and result.ev_kwh_needed <= 0:
            decision = ChargeDecision(target_current=0.0, reason="EV has reached target SoC – stopping")

        elif mode == ChargeMode.ASAP:
            decision = strategy_asap(self.max_current)

        elif mode == ChargeMode.SOLAR_EXCESS:
            prev_current = self.data.applied_current if self.data else 0.0
            ev_charging_kw = (prev_current * self.phases * self.voltage) / 1000.0
            decision = strategy_solar_excess(
                solar_power_kw=effective_solar_kw,
                min_current=self.min_current,
                max_current=self.max_current,
                phases=self.phases,
                voltage=self.voltage,
                grid_export_kw=result.grid_export_kw,
                ev_charging_kw=ev_charging_kw,
            )

        elif mode == ChargeMode.SOLAR_PRICE_BLEND:
            prev_current = self.data.applied_current if self.data else 0.0
            ev_charging_kw = (prev_current * self.phases * self.voltage) / 1000.0
            hours = result.charge_hours_needed if result.charge_hours_needed is not None else DEFAULT_CHARGE_HOURS
            charge_slots = max(1, round(hours * slots_per_hour))
            decision = strategy_solar_price_blend(
                solar_power_kw=effective_solar_kw,
                min_current=self.min_current,
                max_current=self.max_current,
                phases=self.phases,
                voltage=self.voltage,
                price_awareness=self.price_awareness,
                current_price=result.current_import_price or 0.0,
                hourly_prices=future_prices or result.hourly_prices,
                charge_hours_needed=charge_slots,
                grid_export_kw=result.grid_export_kw,
                ev_charging_kw=ev_charging_kw,
                hourly_solar_forecast=result.hourly_solar_forecast or None,
            )

        else:  # MINIMIZE_COST
            hours = result.charge_hours_needed if result.charge_hours_needed is not None else DEFAULT_CHARGE_HOURS
            charge_slots = max(1, round(hours * slots_per_hour))
            min_charging_kw = (self.min_current * self.phases * self.voltage) / 1000.0

            decision = strategy_minimize_cost(
                current_price=result.current_import_price or 0.0,
                hourly_prices=future_prices or result.hourly_prices,
                min_current=self.min_current,
                max_current=self.max_current,
                charge_hours_needed=charge_slots,
                phases=self.phases,
                voltage=self.voltage,
                hourly_solar_forecast=result.hourly_solar_forecast or None,
                solar_excess_kw=solar_excess_kw,
                export_price=result.current_export_price,
            )

            # Build session plan string for MINIMIZE_COST
            if today_prices:
                current_slot_in_today = now.hour * slots_per_hour
                ep: list[float] = []
                all_prices = today_prices + tomorrow_prices
                for i, price in enumerate(all_prices):
                    fi_hour = (i - current_slot_in_today) // slots_per_hour
                    solar_kw = (
                        result.hourly_solar_forecast[fi_hour]
                        if result.hourly_solar_forecast and 0 <= fi_hour < len(result.hourly_solar_forecast)
                        else 0.0
                    )
                    ep.append(
                        result.current_export_price
                        if solar_kw >= min_charging_kw and result.current_export_price is not None
                        else price
                    )
                result.charge_plan = _session_plan(
                    ep, charge_slots, len(today_prices), current_slot_in_today, slots_per_hour
                )

        # Round to whole amperes; apply dead-band to suppress minor fluctuations
        target_amps = round(decision.target_current)
        prev_applied = int(self.data.applied_current) if self.data else 0
        if target_amps > 0 and prev_applied > 0 and abs(target_amps - prev_applied) <= self.charge_deadband:
            target_amps = prev_applied
        result.applied_current = float(target_amps)
        result.charge_reason = decision.reason

        # --- Apply to charger entity ---
        await self._async_set_charger_current(float(target_amps))

        return result

    def _compute_ev_kwh_needed(self) -> float | None:
        """Return the kWh still needed to reach the target SoC, or None if data is missing."""
        def _read_float(entity_id: str | None) -> float | None:
            if not entity_id:
                return None
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                return None
            try:
                return float(state.state)
            except ValueError:
                LOGGER.warning("Could not parse numeric value from %s: %s", entity_id, state.state)
                return None

        capacity_kwh = _read_float(self.ev_battery_capacity_entity)
        target_soc = _read_float(self.ev_target_soc_entity)
        current_soc = _read_float(self.ev_soc_entity)

        if capacity_kwh is None or target_soc is None or current_soc is None:
            return None

        kwh_needed = capacity_kwh * max(0.0, target_soc - current_soc) / 100.0
        return round(kwh_needed, 2)

    async def _async_set_charger_current(self, current: float) -> None:
        """Write the computed current to the charger number entity."""
        charger_state = self.hass.states.get(self.charger_entity)
        if charger_state is None or charger_state.state in ("unavailable", "unknown"):
            LOGGER.warning(
                "Charger entity %s is not available – skipping current update",
                self.charger_entity,
            )
            return

        try:
            current_value = float(charger_state.state)
        except ValueError:
            current_value = None

        if current_value is not None and abs(current_value - current) < 0.05:
            return

        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": self.charger_entity, "value": current},
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "Failed to set charger current on %s to %.1f A: %s",
                self.charger_entity,
                current,
                exc,
            )


def _session_plan(
    effective_prices: list[float],
    charge_slots: int,
    today_slot_count: int,
    current_slot_index: int,
    slots_per_hour: int,
) -> str:
    """Summarise the charge session plan as a human-readable string.

    effective_prices covers today_prices + tomorrow_prices, with export_price
    already substituted for solar-covered slots. charge_slots is already scaled
    for sub-hourly granularity.
    """
    if not effective_prices or charge_slots <= 0:
        return ""

    n = max(1, min(charge_slots, len(effective_prices)))
    sorted_prices = sorted(effective_prices)
    threshold = sorted_prices[n - 1]

    future_today: list[int] = []
    tomorrow_all: list[int] = []
    for i, p in enumerate(effective_prices):
        if i < today_slot_count:
            if i >= current_slot_index and p <= threshold:
                future_today.append(i)
        else:
            if p <= threshold:
                tomorrow_all.append(i)

    today_hours = len(future_today) / slots_per_hour
    tomorrow_hours = len(tomorrow_all) / slots_per_hour

    if today_hours <= 0 and tomorrow_hours > 0:
        return f"Session deferred to tomorrow ({tomorrow_hours:.1f} h planned)"
    if today_hours > 0 and tomorrow_hours > 0:
        return f"Today {today_hours:.1f} h + tomorrow {tomorrow_hours:.1f} h"
    if today_hours > 0:
        return f"Today {today_hours:.1f} h planned"
    return "No cheap slots found in price window"


def _extract_solar_forecast(
    hass: "HomeAssistant",
    entity_ids: list[str],
    base_load_w: float,
) -> list[float]:
    """Aggregate Forecast.Solar entities into an hourly available-kW list.

    Returns a list indexed by hour offset from the current hour (0 = now).
    Each value is the available surplus kW after deducting base_load_w.
    """
    now = dt_util.now().replace(minute=0, second=0, microsecond=0)
    aggregated: dict[int, float] = {}
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            continue
        watts_dict = state.attributes.get("watts")
        if not isinstance(watts_dict, dict):
            continue
        for ts_str, watts in watts_dict.items():
            dt = dt_util.parse_datetime(ts_str)
            if dt is None:
                continue
            dt_hour = dt.replace(minute=0, second=0, microsecond=0)
            offset = round((dt_hour - now).total_seconds() / 3600)
            if 0 <= offset < 48:
                try:
                    aggregated[offset] = aggregated.get(offset, 0.0) + float(watts)
                except (TypeError, ValueError):
                    pass
    if not aggregated:
        return []
    return [
        round(max(0.0, (aggregated.get(i, 0.0) - base_load_w) / 1000.0), 3)
        for i in range(max(aggregated) + 1)
    ]


def _extract_price_list(
    attrs: dict[str, Any], candidate_keys: tuple[str, ...]
) -> list[float]:
    """Try each key in order; return the first list of numeric values found."""
    for key in candidate_keys:
        raw = attrs.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            prices = []
            for item in raw:
                if isinstance(item, dict):
                    val = item.get("value") or item.get("price")
                else:
                    val = item
                try:
                    prices.append(float(val))
                except (TypeError, ValueError):
                    pass
            if prices:
                return prices
    return []
