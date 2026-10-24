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
CONF_CHARGER_ENERGY_ENTITY = "charger_energy_entity"

# Options keys (stored in entry.options, changeable at runtime)
CONF_CHARGE_MODE = "charge_mode"
CONF_CHARGE_DEADBAND = "charge_deadband"
CONF_DAILY_KWH = "daily_kwh"
CONF_FIXED_DAILY_TARGET_SOC = "fixed_daily_target_soc"
CONF_DAILY_RESET_HOUR = "daily_reset_hour"
CONF_DEFER_MIN_SAVING = "defer_min_saving"
CONF_OPPORTUNISTIC_PRICE = "opportunistic_price"
CONF_OPPORTUNISTIC_KWH = "opportunistic_kwh"

# Defaults
DEFAULT_MIN_CURRENT = 6
DEFAULT_MAX_CURRENT = 16
DEFAULT_PHASES = 1
DEFAULT_VOLTAGE = 230
DEFAULT_CHARGE_DEADBAND = 1.0
DEFAULT_BASE_LOAD_W = 500
DEFAULT_DAILY_KWH = 10.0
DEFAULT_FIXED_DAILY_TARGET_SOC = 100.0
DEFAULT_DAILY_RESET_HOUR = 0
DEFAULT_DEFER_MIN_SAVING = 50  # percent; 100 disables deferral
DEFAULT_OPPORTUNISTIC_PRICE = 0.0
DEFAULT_OPPORTUNISTIC_KWH = 0.0

# Update interval in minutes
UPDATE_INTERVAL_MINUTES = 5


class ChargeMode(StrEnum):
    """Available charge strategies."""

    ASAP = "asap"
    SOLAR_EXCESS = "solar_excess"
    FIXED_DAILY_AMOUNT = "fixed_daily_amount"


CHARGE_MODES = [
    ChargeMode.ASAP,
    ChargeMode.SOLAR_EXCESS,
    ChargeMode.FIXED_DAILY_AMOUNT,
]
DEFAULT_CHARGE_MODE = ChargeMode.ASAP
