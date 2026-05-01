"""Select platform – charge mode selector."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

from .const import CHARGE_MODES, DOMAIN, ChargeMode
from .entity import EVChargerManagerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EVChargerManagerCoordinator
    from .data import EVChargerManagerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EVChargerManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the charge mode select entity."""
    async_add_entities(
        [EVChargerModeSelect(coordinator=entry.runtime_data.coordinator)]
    )


class EVChargerModeSelect(EVChargerManagerEntity, SelectEntity):
    """Select entity for choosing the active charge strategy."""

    _attr_options = [mode.value for mode in CHARGE_MODES]
    _attr_icon = "mdi:ev-station"
    _attr_translation_key = "charge_mode"

    def __init__(self, coordinator: EVChargerManagerCoordinator) -> None:
        super().__init__(coordinator, unique_id_suffix="charge_mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.current_mode.value

    async def async_select_option(self, option: str) -> None:
        """Persist the new mode and trigger an immediate coordinator update."""
        self.coordinator.current_mode = ChargeMode(option)
        await self.coordinator.async_request_refresh()
