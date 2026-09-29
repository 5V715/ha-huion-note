"""'Sync now' button — for when you don't want to wait for the next advertisement."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HuionNoteConfigEntry
from .entity import HuionNoteEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HuionNoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HuionNoteSyncButton(entry.runtime_data, "sync")])


class HuionNoteSyncButton(HuionNoteEntity, ButtonEntity):
    async def async_press(self) -> None:
        if not await self.coordinator.async_sync():
            raise HomeAssistantError(
                f"Sync failed: {self.coordinator.data.last_error or 'already running'}"
            )
