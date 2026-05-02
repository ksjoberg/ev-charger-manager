"""Number platform – configurable runtime parameters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfElectricCurrent

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
    async_add_entities(
        [
            EVChargerMinCurrentNumber(coordinator),
            EVChargerMaxCurrentNumber(coordinator),
        ]
    )


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
        super().__init__(coordinator, unique_id_suffix="min_current")

    @property
    def native_value(self) -> float:
        return float(self.coordinator.min_current)

    async def async_set_native_value(self, value: float) -> None:
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
        super().__init__(coordinator, unique_id_suffix="max_current")

    @property
    def native_value(self) -> float:
        return float(self.coordinator.max_current)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.max_current = float(round(value))
        await self.coordinator.async_request_refresh()


