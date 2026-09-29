"""Sensors: sync status, last sync, pages saved, battery."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HuionNoteConfigEntry
from .const import STATUS_ERROR, STATUS_IDLE, STATUS_SYNCING
from .coordinator import HuionNoteData
from .entity import HuionNoteEntity


@dataclass(frozen=True, kw_only=True)
class HuionSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HuionNoteData], Any]
    attrs_fn: Callable[[HuionNoteData], dict[str, Any]] | None = None


SENSORS: tuple[HuionSensorDescription, ...] = (
    HuionSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_IDLE, STATUS_SYNCING, STATUS_ERROR],
        value_fn=lambda d: d.status,
        attrs_fn=lambda d: {"last_error": d.last_error},
    ),
    HuionSensorDescription(
        key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.last_sync,
        attrs_fn=lambda d: {"new_pages": d.last_new_pages, "latest_page": d.latest_page},
    ),
    HuionSensorDescription(
        key="pages_saved",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.total_pages,
    ),
    HuionSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HuionNoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(HuionNoteSensor(entry.runtime_data, d) for d in SENSORS)


class HuionNoteSensor(HuionNoteEntity, SensorEntity):
    entity_description: HuionSensorDescription

    def __init__(self, coordinator, description: HuionSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
