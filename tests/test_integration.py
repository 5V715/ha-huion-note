"""Home Assistant integration tests (pytest-homeassistant-custom-component)."""
from __future__ import annotations

import asyncio
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
    with t_patch as bt, d_patch:
        coordinator._async_handle_advertisement(service_info(), BluetoothChange.ADVERTISEMENT)
        # A second advertisement while syncing must not start another sync.
        coordinator._async_handle_advertisement(service_info(), BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert bt.call_count == 1

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


THREE_PAGES = handshake(pages=2) + [count(1), p87(1), count(1), p87(1, x=5), count(1), p87(1, x=9)]


def pngs(path):
    return sorted(f for f in os.listdir(path) if f.endswith(".png"))


async def test_resync_dedupes_and_deletes(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **{CONF_DELETE_AFTER_SYNC: True})
    coordinator = entry.runtime_data
    saved_events = async_capture_events(hass, EVENT_PAGE_SAVED)

    first = FakeTablet(THREE_PAGES)
    t_patch, d_patch = _patch_tablet(first)
    with t_patch, d_patch:
        assert await coordinator.async_sync()
    assert len(saved_events) == 3
    # Highest index first, and the current (last) page is never deleted.
    assert [fr[3] for fr in first.ops(OrderCode.DELETE_PAGE)] == [1, 0]

    second = FakeTablet(THREE_PAGES)  # same content again (e.g. delete didn't stick)
    t_patch, d_patch = _patch_tablet(second)
    with t_patch, d_patch:
        assert await coordinator.async_sync()
    assert len(saved_events) == 3  # nothing new written
    assert [fr[3] for fr in second.ops(OrderCode.DELETE_PAGE)] == [1, 0]
    assert len(pngs(tmp_path)) == 3


async def test_known_page_with_missing_files_is_saved_again_before_delete(
    hass: HomeAssistant, tmp_path
) -> None:
    """Review finding: a page seen before was deleted even though its files were gone."""
    entry = await _setup(hass, tmp_path)  # delete off
    t_patch, d_patch = _patch_tablet(FakeTablet(THREE_PAGES))
    with t_patch, d_patch:
        assert await entry.runtime_data.async_sync()
    for f in os.listdir(tmp_path):  # user moved the files away
        os.remove(tmp_path / f)

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_DELETE_AFTER_SYNC: True}
    )
    await hass.async_block_till_done()
    tablet = FakeTablet(THREE_PAGES)
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch:
        assert await entry.runtime_data.async_sync()
    assert len(pngs(tmp_path)) == 3  # re-written before anything was deleted
    assert [fr[3] for fr in tablet.ops(OrderCode.DELETE_PAGE)] == [1, 0]


async def test_failed_write_deletes_nothing(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **{CONF_DELETE_AFTER_SYNC: True})
    tablet = FakeTablet(THREE_PAGES)
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch, patch(
        "custom_components.huion_note.coordinator.write_page", side_effect=OSError("disk full")
    ):
        assert not await entry.runtime_data.async_sync()
    assert not tablet.ops(OrderCode.DELETE_PAGE)
    assert entry.runtime_data.data.status == "error"
    assert entry.runtime_data.data.known_pages == {}


def dot_page(x):
    """One packet holding a single dot: pen-down point, then pen-up point."""
    down = bytes([x, 0x01, 0x10, 0x00, 0x05, 0x20])
    up = bytes([x, 0x01, 0x10, 0x00, 0x00, 0x00])
    return bytes([0xCD, 0x87, 0x7E, 1, 0]) + down + up + bytes([0xEE])


async def test_dot_only_pages_are_distinct_and_kept_on_tablet(hass: HomeAssistant, tmp_path) -> None:
    """Review finding: pages decoding to no strokes shared one digest and were deleted."""
    entry = await _setup(hass, tmp_path, **{CONF_DELETE_AFTER_SYNC: True})
    tablet = FakeTablet(handshake(pages=2) + [count(1), dot_page(1), count(1), dot_page(2),
                                              count(1), p87(1)])
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch:
        assert await entry.runtime_data.async_sync()
    assert len(pngs(tmp_path)) == 3
    assert not tablet.ops(OrderCode.DELETE_PAGE)  # 0,1 have no strokes; 2 is current


async def test_disconnect_error_still_saves_progress(hass: HomeAssistant, tmp_path) -> None:
    """Review finding: an error from close() left status 'syncing' and skipped the save."""
    entry = await _setup(hass, tmp_path)
    coordinator = entry.runtime_data
    tablet = FakeTablet(THREE_PAGES)

    async def bad_close():
        tablet.closed = True
        raise TimeoutError("disconnect timed out")

    tablet.close = bad_close
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch, d_patch:
        assert await coordinator.async_sync()
    assert coordinator.data.status == "idle"
    stored = await coordinator._store.async_load()
    assert len(stored["known_pages"]) == 3


class StallingTablet(FakeTablet):
    """connect() blocks until cancelled — a sync that is still running."""

    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()

    async def connect(self):
        self.started.set()
        await asyncio.Event().wait()


async def test_button_sync_is_cancelled_on_unload(hass: HomeAssistant, tmp_path) -> None:
    """Review finding: a button-started sync outlived an unload/reload."""
    entry = await _setup(hass, tmp_path)
    tablet = StallingTablet()
    t_patch, d_patch = _patch_tablet(tablet)
    with t_patch as bt, d_patch:
        press = hass.async_create_task(hass.services.async_call(
            "button", "press", {"entity_id": "button.huion_note_x10_sync_now"}, blocking=True))
        try:
            await tablet.started.wait()
            # An advertisement during the button's sync joins it instead of starting another.
            entry.runtime_data._async_handle_advertisement(
                service_info(), BluetoothChange.ADVERTISEMENT)
            assert bt.call_count == 1

            assert await hass.config_entries.async_unload(entry.entry_id)
            assert tablet.closed
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(press, 5)
        finally:
            press.cancel()  # on regression: don't leave a stuck sync to hang teardown


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
