"""Sensor platform for EV Charger Manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower

from .data import EVChargerData
from .entity import EVChargerManagerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EVChargerManagerCoordinator
    from .data import EVChargerManagerConfigEntry


@dataclass(frozen=True, kw_only=True)
class EVChargerSensorDescription(SensorEntityDescription):
    """Extended description that includes a value extractor."""

    value_fn: object = None  # Callable[[EVChargerData], StateType]


GRID_EXPORT_SENSORS: tuple[EVChargerSensorDescription, ...] = (
    EVChargerSensorDescription(
        key="grid_export",
        translation_key="grid_export",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
        value_fn=lambda d: round(d.grid_export_kw, 2) if d.grid_export_kw is not None else None,
    ),
)

SENSOR_DESCRIPTIONS: tuple[EVChargerSensorDescription, ...] = (
    EVChargerSensorDescription(
        key="applied_current",
        translation_key="applied_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        value_fn=lambda d: round(d.applied_current, 1),
    ),
    EVChargerSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        value_fn=lambda d: round(d.solar_power_kw, 2),
    ),
    EVChargerSensorDescription(
        key="current_price",
        translation_key="current_price",
        # Unit intentionally omitted – it depends on the Nordpool entity's currency
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash-clock",
        value_fn=lambda d: round(d.current_price, 4) if d.current_price is not None else None,
    ),
    EVChargerSensorDescription(
        key="charge_reason",
        translation_key="charge_reason",
        icon="mdi:information-outline",
        value_fn=lambda d: d.charge_reason,
    ),
    EVChargerSensorDescription(
        key="ev_kwh_needed",
        translation_key="ev_kwh_needed",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.ev_kwh_needed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EVChargerManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Charger Manager sensor entities."""
    coordinator = entry.runtime_data.coordinator
    descriptions = list(SENSOR_DESCRIPTIONS)
    if coordinator.grid_power_entity:
        descriptions.extend(GRID_EXPORT_SENSORS)
    async_add_entities(
        EVChargerManagerSensor(coordinator=coordinator, description=desc)
        for desc in descriptions
    )


class EVChargerManagerSensor(EVChargerManagerEntity, SensorEntity):
    """A sensor that reads a computed value from the coordinator data."""

    entity_description: EVChargerSensorDescription

    def __init__(
        self,
        coordinator: EVChargerManagerCoordinator,
        description: EVChargerSensorDescription,
    ) -> None:
        super().__init__(coordinator, unique_id_suffix=description.key)
        self.entity_description = description

    @property
    def native_value(self) -> object:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)  # type: ignore[call-arg]
