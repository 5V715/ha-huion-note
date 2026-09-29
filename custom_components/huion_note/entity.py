"""Shared entity base."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HuionNoteCoordinator


class HuionNoteEntity(CoordinatorEntity[HuionNoteCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HuionNoteCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.config_entry.title,
            manufacturer="Huion",
            model="Note X10",
        )

    @property
    def available(self) -> bool:
        # The tablet sleeps most of the time; our state (last sync, files) is still valid.
        return True
