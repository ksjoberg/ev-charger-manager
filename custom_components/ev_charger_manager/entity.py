"""Base entity class for EV Charger Manager."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EVChargerManagerCoordinator


class EVChargerManagerEntity(CoordinatorEntity[EVChargerManagerCoordinator]):
    """Base class shared by all EV Charger Manager entities."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EVChargerManagerCoordinator,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.config_entry.title,
            manufacturer="EV Charger Manager",
            model="Smart Charge Controller",
            entry_type=None,
        )
