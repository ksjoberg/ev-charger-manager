"""Number platform – configurable runtime parameters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)

from .entity import EVChargerManagerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EVChargerManagerCoordinator
    from .data import EVChargerManagerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EVChargerManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Charger Manager number entities."""
    coordinator = entry.runtime_data.coordinator
    entities: list[NumberEntity] = [
        EVChargerMinCurrentNumber(coordinator),
        EVChargerMaxCurrentNumber(coordinator),
        EVChargerDeadbandNumber(coordinator),
        EVChargerDailyKwhNumber(coordinator),
        EVChargerFixedDailyTargetSocNumber(coordinator),
        EVChargerDailyResetHourNumber(coordinator),
        EVChargerDeferMinSavingNumber(coordinator),
        EVChargerOpportunisticPriceNumber(coordinator),
        EVChargerOpportunisticKwhNumber(coordinator),
    ]
    if coordinator.forecast_solar_entities:
        entities.append(EVChargerBaseLoadNumber(coordinator))
    async_add_entities(entities)


class EVChargerMinCurrentNumber(EVChargerManagerEntity, NumberEntity):
    """Runtime-adjustable lower bound for the charge current."""

    _attr_icon = "mdi:current-ac"
    _attr_native_min_value = 0
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "min_current"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="min_current")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.min_current)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.min_current = float(round(value))
        await self.coordinator.async_request_refresh()


class EVChargerMaxCurrentNumber(EVChargerManagerEntity, NumberEntity):
    """Runtime-adjustable upper bound for the charge current."""

    _attr_icon = "mdi:current-ac"
    _attr_native_min_value = 1
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "max_current"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="max_current")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.max_current)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.max_current = float(round(value))
        await self.coordinator.async_request_refresh()


class EVChargerDeadbandNumber(EVChargerManagerEntity, NumberEntity):
    """Dead-band that suppresses minor current changes to reduce charger churn."""

    _attr_icon = "mdi:sine-wave"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5.0
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "charge_deadband"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="charge_deadband")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.charge_deadband)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.charge_deadband = (
            round(float(value) * 2) / 2
        )  # snap to 0.5 steps
        await self.coordinator.async_request_refresh()


class EVChargerBaseLoadNumber(EVChargerManagerEntity, NumberEntity):
    """Household base load deducted from solar forecast to determine available charging power."""

    _attr_icon = "mdi:home-lightning-bolt"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5000.0
    _attr_native_step = 50.0
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "base_load_w"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="base_load_w")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.base_load_w)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.base_load_w = round(float(value) / 50) * 50
        await self.coordinator.async_request_refresh()


class EVChargerDailyKwhNumber(EVChargerManagerEntity, NumberEntity):
    """Energy to deliver per daily window in Fixed Daily Amount mode."""

    _attr_icon = "mdi:battery-plus-variant"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key = "daily_kwh"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="daily_kwh")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.daily_kwh)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.daily_kwh = round(float(value), 1)
        await self.coordinator.async_request_refresh()


class EVChargerFixedDailyTargetSocNumber(EVChargerManagerEntity, NumberEntity):
    """SoC ceiling for the base fixed daily amount."""

    _attr_icon = "mdi:battery-medium"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "fixed_daily_target_soc"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="fixed_daily_target_soc")

    @property
    def native_value(self) -> float:
        """Return the fixed daily SoC ceiling."""
        return float(self.coordinator.fixed_daily_target_soc)

    async def async_set_native_value(self, value: float) -> None:
        """Store the target and refresh the coordinator."""
        self.coordinator.fixed_daily_target_soc = float(round(value))
        await self.coordinator.async_request_refresh()


class EVChargerDailyResetHourNumber(EVChargerManagerEntity, NumberEntity):
    """Local hour at which the daily window starts (and the previous one is due)."""

    _attr_icon = "mdi:clock-start"
    _attr_native_min_value = 0
    _attr_native_max_value = 23
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "daily_reset_hour"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="daily_reset_hour")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.daily_reset_hour)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.daily_reset_hour = round(value)
        await self.coordinator.async_request_refresh()


class EVChargerDeferMinSavingNumber(EVChargerManagerEntity, NumberEntity):
    """How much cheaper tomorrow must be before today's charging is deferred."""

    _attr_icon = "mdi:calendar-arrow-right"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "defer_min_saving"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="defer_min_saving")

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.defer_min_saving)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value and refresh the coordinator."""
        self.coordinator.defer_min_saving = float(round(value))
        await self.coordinator.async_request_refresh()


class EVChargerOpportunisticPriceNumber(EVChargerManagerEntity, NumberEntity):
    """Maximum import price for opportunistic charging."""

    _attr_icon = "mdi:cash-check"
    _attr_native_min_value = -100.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "opportunistic_price"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="opportunistic_price")

    @property
    def native_value(self) -> float:
        """Return the configured all-in import price threshold."""
        return float(self.coordinator.opportunistic_price)

    async def async_set_native_value(self, value: float) -> None:
        """Store the threshold and refresh the coordinator."""
        self.coordinator.opportunistic_price = round(float(value), 2)
        await self.coordinator.async_request_refresh()


class EVChargerOpportunisticKwhNumber(EVChargerManagerEntity, NumberEntity):
    """Extra energy allowed when the import price meets the threshold."""

    _attr_icon = "mdi:battery-plus"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key = "opportunistic_kwh"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, unique_id_suffix="opportunistic_kwh")

    @property
    def native_value(self) -> float:
        """Return the configured extra energy allowance."""
        return float(self.coordinator.opportunistic_kwh)

    async def async_set_native_value(self, value: float) -> None:
        """Store the allowance and refresh the coordinator."""
        self.coordinator.opportunistic_kwh = round(float(value), 1)
        await self.coordinator.async_request_refresh()
