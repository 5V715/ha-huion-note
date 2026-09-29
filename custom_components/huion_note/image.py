"""Image entity showing the most recently synced page."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HuionNoteConfigEntry
from .coordinator import HuionNoteCoordinator
from .entity import HuionNoteEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HuionNoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HuionNoteLatestPage(hass, entry.runtime_data)])


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


class HuionNoteLatestPage(HuionNoteEntity, ImageEntity):
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: HuionNoteCoordinator) -> None:
        HuionNoteEntity.__init__(self, coordinator, "latest_page")
        ImageEntity.__init__(self, hass)
        self._path = coordinator.data.latest_page
        self._attr_image_last_updated = coordinator.data.last_sync

    @callback
    def _handle_coordinator_update(self) -> None:
        path = self.coordinator.data.latest_page
        if path != self._path:
            self._path = path
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if not self._path:
            return None
        return await self.hass.async_add_executor_job(_read, self._path)
