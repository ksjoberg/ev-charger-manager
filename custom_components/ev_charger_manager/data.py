"""Custom types for EV Charger Manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .coordinator import EVChargerManagerCoordinator


type EVChargerManagerConfigEntry = ConfigEntry[EVChargerManagerRuntimeData]


@dataclass
class EVChargerManagerRuntimeData:
    """Runtime data attached to a config entry."""

    coordinator: EVChargerManagerCoordinator
    integration: Integration


@dataclass
class EVChargerData:
    """Snapshot of all computed values produced by a coordinator update cycle."""

    mode: str = ""
    solar_power_kw: float = 0.0
    current_price: float | None = None
    hourly_prices: list[float] = field(default_factory=list)
    applied_current: float = 0.0
    charge_reason: str = ""
    grid_export_kw: float | None = None
    ev_kwh_needed: float | None = None
