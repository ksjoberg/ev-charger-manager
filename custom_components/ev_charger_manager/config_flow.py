"""Config flow for EV Charger Manager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_CHARGE_HOURS,
    CONF_CHARGE_MODE,
    CONF_CHARGER_CURRENT_ENTITY,
    CONF_EV_BATTERY_CAPACITY_ENTITY,
    CONF_EV_SOC_ENTITY,
    CONF_EV_TARGET_SOC_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_NORDPOOL_ENTITY,
    CONF_PHASES,
    CONF_PV_PEAK_POWER,
    CONF_VOLTAGE,
    CONF_WEATHER_ENTITY,
    DEFAULT_CHARGE_HOURS,
    DEFAULT_CHARGE_MODE,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_VOLTAGE,
    DOMAIN,
    LOGGER,
    ChargeMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult


class EVChargerManagerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EV Charger Manager.

    Step 1 (user)    – charger entity, min/max current, phases, voltage
    Step 2 (solar)   – PV peak power, weather entity, optional grid sensor
    Step 3 (pricing) – Nordpool price entity
    Step 4 (ev)      – EV battery capacity, target SoC, current SoC entities
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1 – Charger setup
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_id = user_input[CONF_CHARGER_CURRENT_ENTITY]
            state = self.hass.states.get(entity_id)
            if state is None:
                errors[CONF_CHARGER_CURRENT_ENTITY] = "entity_not_found"
            elif state.domain != "number":
                errors[CONF_CHARGER_CURRENT_ENTITY] = "not_a_number_entity"
            else:
                self._data.update(user_input)
                return await self.async_step_solar()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHARGER_CURRENT_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="number")
                    ),
                    vol.Optional(
                        CONF_MIN_CURRENT,
                        default=(self._data or {}).get(
                            CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=32,
                            step=1,
                            unit_of_measurement="A",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_CURRENT,
                        default=(self._data or {}).get(
                            CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=32,
                            step=1,
                            unit_of_measurement="A",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_PHASES,
                        default=(self._data or {}).get(CONF_PHASES, DEFAULT_PHASES),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["1", "3"],
                            translation_key="phases",
                        )
                    ),
                    vol.Optional(
                        CONF_VOLTAGE,
                        default=(self._data or {}).get(CONF_VOLTAGE, DEFAULT_VOLTAGE),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=100,
                            max=480,
                            step=1,
                            unit_of_measurement="V",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2 – Solar setup
    # ------------------------------------------------------------------

    async def async_step_solar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            weather_entity = user_input.get(CONF_WEATHER_ENTITY)
            if weather_entity:
                state = self.hass.states.get(weather_entity)
                if state is None:
                    errors[CONF_WEATHER_ENTITY] = "entity_not_found"

            pv_entity = user_input.get(CONF_PV_POWER_ENTITY)
            if pv_entity:
                state = self.hass.states.get(pv_entity)
                if state is None:
                    errors[CONF_PV_POWER_ENTITY] = "entity_not_found"

            grid_entity = user_input.get(CONF_GRID_POWER_ENTITY)
            if grid_entity:
                state = self.hass.states.get(grid_entity)
                if state is None:
                    errors[CONF_GRID_POWER_ENTITY] = "entity_not_found"

            if not errors:
                self._data.update(
                    {k: v for k, v in user_input.items() if v not in (None, "")}
                )
                return await self.async_step_pricing()

        return self.async_show_form(
            step_id="solar",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PV_PEAK_POWER,
                        default=(self._data or {}).get(CONF_PV_PEAK_POWER, 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            unit_of_measurement="kW",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(CONF_PV_POWER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(CONF_GRID_POWER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3 – Pricing / Nordpool
    # ------------------------------------------------------------------

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            nordpool_entity = user_input.get(CONF_NORDPOOL_ENTITY)
            if nordpool_entity:
                state = self.hass.states.get(nordpool_entity)
                if state is None:
                    errors[CONF_NORDPOOL_ENTITY] = "entity_not_found"

            if not errors:
                if nordpool_entity:
                    self._data[CONF_NORDPOOL_ENTITY] = nordpool_entity
                return await self.async_step_ev()

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NORDPOOL_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 4 – EV battery / SoC
    # ------------------------------------------------------------------

    async def async_step_ev(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            for key in (
                CONF_EV_BATTERY_CAPACITY_ENTITY,
                CONF_EV_TARGET_SOC_ENTITY,
                CONF_EV_SOC_ENTITY,
            ):
                entity_id = user_input.get(key)
                if entity_id and self.hass.states.get(entity_id) is None:
                    errors[key] = "entity_not_found"

            if not errors:
                self._data.update(
                    {k: v for k, v in user_input.items() if v not in (None, "")}
                )

                charger_entity = self._data[CONF_CHARGER_CURRENT_ENTITY]
                charger_state = self.hass.states.get(charger_entity)
                title = (
                    charger_state.attributes.get("friendly_name", charger_entity)
                    if charger_state
                    else charger_entity
                )

                min_a = self._data.pop(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)
                max_a = self._data.pop(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)

                return self.async_create_entry(
                    title=f"EV Charger – {title}",
                    data=self._data,
                    options={
                        CONF_CHARGE_MODE: DEFAULT_CHARGE_MODE.value,
                        CONF_CHARGE_HOURS: DEFAULT_CHARGE_HOURS,
                        CONF_MIN_CURRENT: float(min_a),
                        CONF_MAX_CURRENT: float(max_a),
                    },
                )

        return self.async_show_form(
            step_id="ev",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_EV_BATTERY_CAPACITY_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                    vol.Optional(CONF_EV_TARGET_SOC_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                    vol.Optional(CONF_EV_SOC_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Options flow (reconfigure without removing)
    # ------------------------------------------------------------------

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> EVChargerManagerOptionsFlowHandler:
        return EVChargerManagerOptionsFlowHandler()


class EVChargerManagerOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow: tweak entity references and behaviour after initial setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate entities that were provided
            for key in (
                CONF_CHARGER_CURRENT_ENTITY,
                CONF_WEATHER_ENTITY,
                CONF_PV_POWER_ENTITY,
                CONF_GRID_POWER_ENTITY,
                CONF_NORDPOOL_ENTITY,
                CONF_EV_BATTERY_CAPACITY_ENTITY,
                CONF_EV_TARGET_SOC_ENTITY,
                CONF_EV_SOC_ENTITY,
            ):
                entity_id = user_input.get(key)
                if entity_id and self.hass.states.get(entity_id) is None:
                    errors[key] = "entity_not_found"

            if not errors:
                new_data = {**self.config_entry.data}
                new_options = {**self.config_entry.options}

                for key in (
                    CONF_CHARGER_CURRENT_ENTITY,
                    CONF_PHASES,
                    CONF_VOLTAGE,
                    CONF_PV_PEAK_POWER,
                    CONF_WEATHER_ENTITY,
                    CONF_PV_POWER_ENTITY,
                    CONF_GRID_POWER_ENTITY,
                    CONF_NORDPOOL_ENTITY,
                    CONF_EV_BATTERY_CAPACITY_ENTITY,
                    CONF_EV_TARGET_SOC_ENTITY,
                    CONF_EV_SOC_ENTITY,
                ):
                    val = user_input.get(key)
                    if val not in (None, ""):
                        new_data[key] = val

                new_options[CONF_MIN_CURRENT] = float(
                    user_input.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)
                )
                new_options[CONF_MAX_CURRENT] = float(
                    user_input.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
                )
                new_options[CONF_CHARGE_HOURS] = int(
                    user_input.get(CONF_CHARGE_HOURS, DEFAULT_CHARGE_HOURS)
                )

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title="", data=new_options)

        data = self.config_entry.data
        opts = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHARGER_CURRENT_ENTITY,
                        default=data.get(CONF_CHARGER_CURRENT_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="number")
                    ),
                    vol.Optional(
                        CONF_MIN_CURRENT,
                        default=opts.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=32, step=1, unit_of_measurement="A",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_CURRENT,
                        default=opts.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=32, step=1, unit_of_measurement="A",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_PHASES,
                        default=str(data.get(CONF_PHASES, DEFAULT_PHASES)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["1", "3"],
                            translation_key="phases",
                        )
                    ),
                    vol.Optional(
                        CONF_VOLTAGE,
                        default=data.get(CONF_VOLTAGE, DEFAULT_VOLTAGE),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=100, max=480, step=1, unit_of_measurement="V",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_PV_PEAK_POWER,
                        default=data.get(CONF_PV_PEAK_POWER, 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=100, step=0.1, unit_of_measurement="kW",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_WEATHER_ENTITY,
                        default=data.get(CONF_WEATHER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(
                        CONF_PV_POWER_ENTITY,
                        default=data.get(CONF_PV_POWER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_GRID_POWER_ENTITY,
                        default=data.get(CONF_GRID_POWER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_NORDPOOL_ENTITY,
                        default=data.get(CONF_NORDPOOL_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_CHARGE_HOURS,
                        default=opts.get(CONF_CHARGE_HOURS, DEFAULT_CHARGE_HOURS),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=24, step=1,
                            unit_of_measurement="h",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_EV_BATTERY_CAPACITY_ENTITY,
                        default=data.get(CONF_EV_BATTERY_CAPACITY_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                    vol.Optional(
                        CONF_EV_TARGET_SOC_ENTITY,
                        default=data.get(CONF_EV_TARGET_SOC_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                    vol.Optional(
                        CONF_EV_SOC_ENTITY,
                        default=data.get(CONF_EV_SOC_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "number"])
                    ),
                }
            ),
            errors=errors,
        )
