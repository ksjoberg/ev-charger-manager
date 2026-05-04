"""Constants for EV Charger Manager."""

from enum import StrEnum
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "ev_charger_manager"
ATTRIBUTION = "EV Charger Manager"

# Config entry keys (set during config flow, not user-changeable via options)
CONF_CHARGER_CURRENT_ENTITY = "charger_current_entity"
CONF_MIN_CURRENT = "min_current"
CONF_MAX_CURRENT = "max_current"
CONF_PHASES = "phases"
CONF_VOLTAGE = "voltage"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_NORDPOOL_IMPORT_ENTITY = "nordpool_import_entity"
CONF_NORDPOOL_EXPORT_ENTITY = "nordpool_export_entity"
CONF_EV_BATTERY_CAPACITY_ENTITY = "ev_battery_capacity_entity"
CONF_EV_TARGET_SOC_ENTITY = "ev_target_soc_entity"
CONF_EV_SOC_ENTITY = "ev_soc_entity"
CONF_FORECAST_SOLAR_ENTITIES = "forecast_solar_entities"
CONF_BASE_LOAD_W = "base_load_w"

# Options keys (stored in entry.options, changeable at runtime)
CONF_CHARGE_MODE = "charge_mode"
CONF_CHARGE_HOURS = "charge_hours_needed"
CONF_PRICE_AWARENESS = "price_awareness"
CONF_CHARGE_DEADBAND = "charge_deadband"

# Defaults
DEFAULT_MIN_CURRENT = 6
DEFAULT_MAX_CURRENT = 16
DEFAULT_PHASES = 1
DEFAULT_VOLTAGE = 230
DEFAULT_CHARGE_HOURS = 4
DEFAULT_PRICE_AWARENESS = 0.5
DEFAULT_CHARGE_DEADBAND = 1.0
DEFAULT_BASE_LOAD_W = 500

# Update interval in minutes
UPDATE_INTERVAL_MINUTES = 5


class ChargeMode(StrEnum):
    ASAP = "asap"
    SOLAR_EXCESS = "solar_excess"
    MINIMIZE_COST = "minimize_cost"
    SOLAR_PRICE_BLEND = "solar_price_blend"


CHARGE_MODES = [ChargeMode.ASAP, ChargeMode.SOLAR_EXCESS, ChargeMode.MINIMIZE_COST, ChargeMode.SOLAR_PRICE_BLEND]
DEFAULT_CHARGE_MODE = ChargeMode.ASAP

# Attribute names tried when reading hourly prices from Nordpool-style sensors
NORDPOOL_PRICE_ATTRS = ("today", "raw_today", "prices_today", "prices")
NORDPOOL_TOMORROW_ATTRS = ("tomorrow", "raw_tomorrow", "prices_tomorrow")
