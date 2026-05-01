"""EV Charger Manager – smart charge controller for Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.loader import async_get_loaded_integration

from .const import DOMAIN, LOGGER
from .coordinator import EVChargerManagerCoordinator
from .data import EVChargerManagerRuntimeData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EVChargerManagerConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SELECT,
    Platform.SENSOR,
    Platform.NUMBER,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EVChargerManagerConfigEntry,
) -> bool:
    """Set up EV Charger Manager from a config entry."""
    coordinator = EVChargerManagerCoordinator(hass=hass, entry=entry)

    entry.runtime_data = EVChargerManagerRuntimeData(
        coordinator=coordinator,
        integration=async_get_loaded_integration(hass, entry.domain),
    )

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EVChargerManagerConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EVChargerManagerConfigEntry,
) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
