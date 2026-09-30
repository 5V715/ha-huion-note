"""Pairing housekeeping on a local BlueZ adapter.

The notebook only keeps a connection from a central it is paired with. When it
pairs with another device (e.g. the Huion app on a phone) it forgets our pairing,
while BlueZ keeps its half: every connect then times out until that stale
pairing is removed. Both helpers are no-ops for devices seen through an ESPHome
proxy (no BlueZ object path) and never raise.
"""
from __future__ import annotations

import logging

from bleak.backends.device import BLEDevice
from bleak_retry_connector.bluez import path_from_ble_device

_LOGGER = logging.getLogger(__name__)


async def is_paired(device: BLEDevice) -> bool:
    """True if the local BlueZ adapter holds a pairing for `device`."""
    if not (path := path_from_ble_device(device)):
        return False
    try:
        from bleak.backends.bluezdbus.manager import get_global_bluez_manager

        manager = await get_global_bluez_manager()
        return manager.is_paired(path)
    except Exception:  # noqa: BLE001 — no BlueZ / D-Bus here: treat as not paired
        _LOGGER.debug("%s: could not read BlueZ pairing state", device.address, exc_info=True)
        return False


async def remove_pairing(device: BLEDevice) -> bool:
    """Remove BlueZ's pairing for `device` (like `bluetoothctl remove`)."""
    try:
        from bleak.backends.bluezdbus.client import BleakClientBlueZDBus

        await BleakClientBlueZDBus(device).unpair()
        return True
    except Exception:  # noqa: BLE001
        _LOGGER.debug("%s: could not remove BlueZ pairing", device.address, exc_info=True)
        return False
