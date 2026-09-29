"""Sync coordinator: pulls the tablet's pages whenever Home Assistant sees it.

Home Assistant's bluetooth integration (local adapter or ESPHome proxy) reports
each advertisement from the configured address; the first one after the cooldown
starts a sync. Entities are CoordinatorEntities fed via async_set_updated_data —
nothing is polled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COOLDOWN,
    CONF_DELETE_AFTER_SYNC,
    CONF_OUTPUT_DIR,
    CONF_PIN,
    DEFAULT_COOLDOWN,
    DEFAULT_SUBDIR,
    DOMAIN,
    EVENT_PAGE_SAVED,
    EVENT_SYNC_FINISHED,
    FAILURE_BACKOFF,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_SYNCING,
)
from .pages import SavedPage, is_saved, page_digest, write_page
from .protocol.codec import Page
from .protocol.errors import AuthFailed, PinRequired, TransportClosed
from .protocol.session import SyncSession
from .transport import BleakTransport

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
MAX_KNOWN_PAGES = 5000
SYNC_TIMEOUT = 15 * 60  # seconds; a full 64-page notebook fits comfortably


@dataclass
class HuionNoteData:
    status: str = STATUS_IDLE
    last_error: str | None = None
    last_sync: datetime | None = None
    last_new_pages: int = 0
    total_pages: int = 0
    battery: int | None = None
    latest_page: str | None = None  # PNG path of the most recently saved page
    # page digest -> file base path (no extension) of its saved copy, oldest first
    known_pages: dict[str, str] = field(default_factory=dict)


def default_output_dir(hass: HomeAssistant) -> str:
    media = hass.config.media_dirs.get("local") or next(
        iter(hass.config.media_dirs.values()), hass.config.path("media")
    )
    return f"{media}/{DEFAULT_SUBDIR}"


class HuionNoteCoordinator(DataUpdateCoordinator[HuionNoteData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=entry.title)
        self.address: str = entry.data[CONF_ADDRESS]
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_success = 0.0  # time.monotonic() stamps
        self._last_failure = 0.0
        self.data = HuionNoteData()

    # --- options -----------------------------------------------------------------

    @property
    def _opts(self) -> dict[str, Any]:
        return self.config_entry.options

    @property
    def output_dir(self) -> str:
        return self._opts.get(CONF_OUTPUT_DIR) or default_output_dir(self.hass)

    # --- lifecycle ---------------------------------------------------------------

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        d = HuionNoteData()
        for key in ("last_new_pages", "total_pages", "battery", "latest_page", "known_pages"):
            if key in stored:
                setattr(d, key, stored[key])
        if stored.get("last_sync"):
            d.last_sync = dt_util.parse_datetime(stored["last_sync"])
        self.data = d

    async def _async_save(self) -> None:
        raw = asdict(self.data)
        raw.pop("status")
        raw.pop("last_error")
        raw["last_sync"] = self.data.last_sync.isoformat() if self.data.last_sync else None
        await self._store.async_save(raw)

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Listen for advertisements from the tablet; returns the unsubscribe."""
        return bluetooth.async_register_callback(
            self.hass,
            self._async_handle_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    async def _async_update_data(self) -> HuionNoteData:
        return self.data  # push-only; never polled

    # --- triggering --------------------------------------------------------------

    @callback
    def _async_handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        if self._task and not self._task.done():
            return
        now = time.monotonic()
        cooldown = self._opts.get(CONF_COOLDOWN, DEFAULT_COOLDOWN) * 60
        if self._last_success and now - self._last_success < cooldown:
            return
        if self._last_failure and now - self._last_failure < FAILURE_BACKOFF:
            return
        _LOGGER.debug("%s: seen (rssi %s) — starting sync", self.address, service_info.rssi)
        self.async_request_sync()

    @callback
    def async_request_sync(self) -> asyncio.Task[bool]:
        """Start a sync (or join the running one). The task belongs to the config
        entry, so unloading/reloading the entry cancels it — a sync never outlives
        the coordinator (and the options) it started with."""
        if self._task is None or self._task.done():
            self._task = self.config_entry.async_create_background_task(
                self.hass, self.async_sync(), f"{DOMAIN} sync {self.address}"
            )
        return self._task

    # --- sync --------------------------------------------------------------------

    def _ble_device(self):
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    async def async_sync(self) -> bool:
        """Pull every page off the tablet. Returns True on success. Never raises
        for device/transport problems — they land in data.status/last_error.
        Call via async_request_sync() so the run is tied to the config entry."""
        if self._lock.locked():
            _LOGGER.debug("%s: sync already running", self.address)
            return False
        async with self._lock:
            ok = await self._async_sync_locked()
        if ok:
            self._last_success = time.monotonic()
        else:
            self._last_failure = time.monotonic()
        return ok

    async def _async_sync_locked(self) -> bool:
        ble_device = self._ble_device()
        if ble_device is None:
            self._set_status(STATUS_ERROR, "tablet not in range of any Bluetooth adapter/proxy")
            return False

        self._set_status(STATUS_SYNCING)
        started = dt_util.now()
        out_dir = self.output_dir
        known = self.data.known_pages
        # Tablet indices whose content is verified on disk, complete and non-empty.
        deletable: list[int] = []
        new_pages: list[SavedPage] = []
        highest = -1  # the current (last) page — never deleted, it may still be written on

        async def on_page(page: Page) -> None:
            nonlocal highest
            highest = max(highest, page.index)
            digest = await self.hass.async_add_executor_job(page_digest, page)
            base = known.get(digest)
            if base and await self.hass.async_add_executor_job(is_saved, base):
                _LOGGER.debug("page %d already saved as %s", page.index, base)
            else:
                if base:
                    _LOGGER.warning(
                        "page %d was saved before as %s but those files are gone — "
                        "saving it again", page.index + 1, base,
                    )
                saved = await self.hass.async_add_executor_job(
                    write_page, page, out_dir, started, digest
                )
                if not await self.hass.async_add_executor_job(is_saved, saved.base):
                    _LOGGER.warning("page %d: files not confirmed on disk — kept on tablet",
                                    page.index + 1)
                    return
                base = known[digest] = saved.base
                new_pages.append(saved)
                self.data.latest_page = saved.png
                self.data.total_pages += 1
                self.hass.bus.async_fire(
                    EVENT_PAGE_SAVED,
                    {
                        "address": self.address,
                        "page": page.index + 1,
                        "strokes": len(page.strokes),
                        "complete": page.complete,
                        "png": saved.png,
                        "svg": saved.svg,
                        "json": saved.json,
                    },
                )
                self.async_update_listeners()
            if not page.complete:
                _LOGGER.warning("page %d incomplete — saved, kept on tablet", page.index + 1)
            elif not page.strokes:
                # Dots only, or something the decoder didn't understand: the points are
                # in the JSON, but keep the tablet's copy rather than trust a blank page.
                _LOGGER.info("page %d has no strokes — kept on tablet", page.index + 1)
            else:
                deletable.append(page.index)

        transport = BleakTransport(
            ble_device, ble_device_callback=lambda: self._ble_device() or ble_device
        )
        session = SyncSession(transport, pin=self._opts.get(CONF_PIN) or None)
        deleted = 0
        try:
            async with asyncio.timeout(SYNC_TIMEOUT):
                await transport.connect()
                total = await session.run(on_page)
                # Re-read the option: never delete on a setting the user just turned off.
                if self._opts.get(CONF_DELETE_AFTER_SYNC, False):
                    # Highest index first so the surviving indices can't shift. The
                    # current page is skipped: strokes added after it was downloaded
                    # would be lost.
                    for idx in sorted(set(deletable) - {highest}, reverse=True):
                        if await session.delete_page(idx):
                            deleted += 1
                        else:
                            _LOGGER.warning("tablet did not confirm delete of page %d", idx + 1)
        except PinRequired:
            return self._fail("tablet requires a PIN — set it in the integration options")
        except AuthFailed as err:
            return self._fail(f"authentication failed: {err}")
        except (TransportClosed, BleakError, TimeoutError) as err:
            return self._fail(f"connection lost: {err or type(err).__name__}")
        except Exception as err:  # noqa: BLE001 — never leave the status stuck on "syncing"
            _LOGGER.exception("%s: unexpected sync error", self.address)
            return self._fail(f"unexpected error: {err}")
        finally:
            try:
                await transport.close()
            except Exception:  # noqa: BLE001 — must not skip saving progress below
                _LOGGER.debug("%s: error while disconnecting", self.address, exc_info=True)
            for digest in list(known)[:-MAX_KNOWN_PAGES]:
                del known[digest]
            if session.battery is not None:
                self.data.battery = session.battery
            await self._async_save()

        _LOGGER.info(
            "%s: synced %d page(s), %d new, %d deleted from tablet",
            self.address, total, len(new_pages), deleted,
        )
        self.data.last_sync = started
        self.data.last_new_pages = len(new_pages)
        await self._async_save()
        self.hass.bus.async_fire(
            EVENT_SYNC_FINISHED,
            {
                "address": self.address,
                "pages_on_tablet": total,
                "new_pages": len(new_pages),
                "deleted": deleted,
                "files": [p.png for p in new_pages],
            },
        )
        self._set_status(STATUS_IDLE)
        return True

    def _fail(self, message: str) -> bool:
        _LOGGER.warning("%s: sync failed: %s", self.address, message)
        self._set_status(STATUS_ERROR, message)
        return False

    @callback
    def _set_status(self, status: str, error: str | None = None) -> None:
        self.data.status = status
        self.data.last_error = error
        self.async_update_listeners()
