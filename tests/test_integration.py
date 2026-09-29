"""Home Assistant integration tests (pytest-homeassistant-custom-component)."""
from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice

from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothChange, BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.huion_note.const import (
    CONF_DELETE_AFTER_SYNC,
    CONF_OUTPUT_DIR,
    DOMAIN,
    EVENT_PAGE_SAVED,
    EVENT_SYNC_FINISHED,
)
from custom_components.huion_note.protocol.frames import OrderCode

from .conftest import FakeTablet, count, handshake, p87

ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE = BLEDevice(ADDRESS, "Huion Note-X10", None)


def service_info(name="Huion Note-X10"):
    return BluetoothServiceInfoBleak(
        name=name, address=ADDRESS, rssi=-60, manufacturer_data={}, service_data={},
        service_uuids=[], source="local", device=DEVICE, advertisement=None,
        connectable=True, time=time.monotonic(), tx_power=None,
    )


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, enable_bluetooth):
    yield


async def test_bluetooth_discovery_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    with patch("custom_components.huion_note.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_flow_no_devices(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options_flow_rejects_bad_pin(hass: HomeAssistant, tmp_path) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OUTPUT_DIR: str(tmp_path), "pin": "12ab"}
    )
    assert result["errors"] == {"pin": "invalid_pin"}


async def _setup(hass, tmp_path, **options):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title="Huion Note-X10",
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_OUTPUT_DIR: str(tmp_path), **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _patch_tablet(tablet):
    return (
        patch("custom_components.huion_note.coordinator.BleakTransport", return_value=tablet),
        patch("custom_components.huion_note.coordinator.bluetooth.async_ble_device_from_address",
              return_value=DEVICE),
    )


async def test_advertisement_triggers_sync(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path)
    coordinator = entry.runtime_data
    saved_events = async_capture_events(hass, EVENT_PAGE_SAVED)
    done_events = async_capture_events(hass, EVENT_SYNC_FINISHED)

    tablet = FakeTablet(handshake(bat=64, pages=1) + [count(1), p87(1), count(2), p87(1), p87(2, x=3)])
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch:
        coordinator._async_handle_advertisement(service_info(), BluetoothChange.ADVERTISEMENT)
        # A second advertisement while syncing must not start another sync.
        coordinator._async_handle_advertisement(service_info(), BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert tablet.connected and tablet.closed
    assert len(saved_events) == 2
    assert all(os.path.isfile(e.data["png"]) for e in saved_events)
    assert done_events[0].data["new_pages"] == 2
    assert not tablet.ops(OrderCode.DELETE_PAGE)  # delete-after-sync is off by default

    assert hass.states.get("sensor.huion_note_x10_sync_status").state == "idle"
    assert hass.states.get("sensor.huion_note_x10_pages_saved").state == "2"
    assert hass.states.get("sensor.huion_note_x10_battery").state == "64"
    assert hass.states.get("sensor.huion_note_x10_last_sync").state not in ("unknown", None)
    assert hass.states.get("image.huion_note_x10_latest_page") is not None

    # Within the cooldown, advertisements are ignored.
    with patch("custom_components.huion_note.coordinator.BleakTransport") as bt:
        coordinator._async_handle_advertisement(service_info(), BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done(wait_background_tasks=True)
        bt.assert_not_called()


async def test_resync_dedupes_and_deletes(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **{CONF_DELETE_AFTER_SYNC: True})
    coordinator = entry.runtime_data
    saved_events = async_capture_events(hass, EVENT_PAGE_SAVED)
    script = handshake(pages=1) + [count(1), p87(1), count(1), p87(1, x=5)]

    first = FakeTablet(script)
    t_patch, d_patch = _patch_tablet(first)
    with t_patch, d_patch:
        assert await coordinator.async_sync()
    assert len(saved_events) == 2
    # highest index first
    assert [fr[3] for fr in first.ops(OrderCode.DELETE_PAGE)] == [1, 0]

    second = FakeTablet(script)  # same content again (e.g. delete didn't stick)
    t_patch, d_patch = _patch_tablet(second)
    with t_patch, d_patch:
        assert await coordinator.async_sync()
    assert len(saved_events) == 2  # nothing new written
    assert len(second.ops(OrderCode.DELETE_PAGE)) == 2
    assert len([f for f in os.listdir(tmp_path) if f.endswith(".png")]) == 2


async def test_sync_failure_sets_error(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path)
    tablet = FakeTablet([handshake()[0], bytes([0xCD, OrderCode.VERIFY_RESULT, 8, 2, 0, 0, 0, 0xED])])
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch:
        assert not await entry.runtime_data.async_sync()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.huion_note_x10_sync_status")
    assert state.state == "error"
    assert "PIN" in state.attributes["last_error"]
    assert tablet.closed


async def test_not_in_range(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path)
    assert not await entry.runtime_data.async_sync()
    assert "not in range" in entry.runtime_data.data.last_error
