"""Huion Note X10 — pull handwritten pages off the notebook over Bluetooth."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import HuionNoteCoordinator

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.IMAGE, Platform.SENSOR]

type HuionNoteConfigEntry = ConfigEntry[HuionNoteCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HuionNoteConfigEntry) -> bool:
    coordinator = HuionNoteCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HuionNoteConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HuionNoteConfigEntry) -> None:
    """Delete the stored sync state and any repair issue. Saved page files stay."""
    await HuionNoteCoordinator(hass, entry).async_remove_storage()


async def _async_reload(hass: HomeAssistant, entry: HuionNoteConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
