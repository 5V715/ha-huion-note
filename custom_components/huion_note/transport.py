"""bleak-backed Transport for protocol.session.SyncSession.

Works with a local BlueZ adapter or an ESPHome Bluetooth proxy — whatever Home
Assistant's bluetooth integration hands us. Notifications from FFE1 (data) and
indications from FFE2 (commands) are funnelled into one queue; the session
dispatches by opcode, so it doesn't care which characteristic a frame came from.

The notebook only keeps a connection from a paired central, so every connect asks
to pair (a no-op once paired); a stale pairing is removed and the connect retried.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import bluez
from .const import CMD_CHAR_UUID, DATA_CHAR_UUID
from .protocol.errors import TransportClosed
from .protocol.frames import heart_beat

_LOGGER = logging.getLogger(__name__)

DISCONNECT_TIMEOUT = 15  # seconds


class BleakTransport:
    def __init__(self, ble_device: BLEDevice,
                 ble_device_callback: Optional[Callable[[], BLEDevice]] = None,
                 keepalive: float = 5.0) -> None:
        self._device = ble_device
        self._device_cb = ble_device_callback
        self._keepalive = keepalive
        self._client: Optional[BleakClientWithServiceCache] = None
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._closed = False
        self._dropped = False  # the notebook (not we) ended the connection
        self._ka_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        try:
            self._client = await self._establish()
            # A failed attempt may have reported its own disconnect; this link is live.
            self._closed = self._dropped = False
            self._queue = asyncio.Queue()
            # No MTU check here: bleak's BlueZ backend only knows a placeholder
            # (23) at this point. The session detects truncated packets instead.
            await self._client.start_notify(DATA_CHAR_UUID, self._on_value)
            await self._client.start_notify(CMD_CHAR_UUID, self._on_value)
        except (BleakError, asyncio.TimeoutError) as err:
            await self.close()
            raise TransportClosed(f"connect failed: {err}") from err
        self._ka_task = asyncio.create_task(self._keepalive_loop())

    async def _establish(self) -> BleakClientWithServiceCache:
        """Connect and pair. If every attempt times out while BlueZ still holds a
        pairing, the notebook has forgotten it (it paired with another device):
        remove our half and try once more, pairing afresh."""
        try:
            return await self._connect_once()
        except (BleakError, asyncio.TimeoutError) as err:
            if not await bluez.is_paired(self._device):
                raise
            _LOGGER.warning(
                "%s: connect failed while paired (%s) — the notebook has probably "
                "forgotten this pairing; removing it and pairing again",
                self._device.address, err,
            )
            await bluez.remove_pairing(self._device)
            return await self._connect_once()

    async def _connect_once(self) -> BleakClientWithServiceCache:
        try:
            return await self._establish_connection(pair=True)
        except NotImplementedError:
            # ESPHome proxies before 2024.3 can't pair; connect unpaired as before.
            _LOGGER.debug("%s: adapter can't pair; connecting unpaired", self._device.address)
            return await self._establish_connection(pair=False)

    async def _establish_connection(self, pair: bool) -> BleakClientWithServiceCache:
        return await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            self._device.name or self._device.address,
            disconnected_callback=self._on_disconnect,
            ble_device_callback=self._device_cb,
            pair=pair,
        )

    def _on_value(self, _char, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    def _on_disconnect(self, _client) -> None:
        _LOGGER.debug("%s: disconnected", self._device.address)
        if not self._closed:
            self._dropped = True
        self._closed = True
        self._queue.put_nowait(None)  # wake any pending recv()

    async def send(self, frame: bytes) -> None:
        if self._closed or not self._client:
            raise TransportClosed(self._closed_reason())
        try:
            await self._client.write_gatt_char(CMD_CHAR_UUID, frame, response=False)
        except BleakError as err:
            raise TransportClosed(f"write failed: {err}") from err

    async def recv(self, timeout: Optional[float] = None) -> bytes:
        item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        if item is None:
            self._queue.put_nowait(None)  # stay closed for later readers
            raise TransportClosed(self._closed_reason())
        return item

    def _closed_reason(self) -> str:
        if self._dropped:
            return ("the notebook closed the connection (it only keeps connections "
                    "from a paired device)")
        return "not connected"

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._keepalive)
                await self.send(heart_beat())
        except (asyncio.CancelledError, TransportClosed):
            pass

    async def close(self) -> None:
        self._closed = True
        if self._ka_task:
            self._ka_task.cancel()
            self._ka_task = None
        if self._client:
            client, self._client = self._client, None
            # BlueZ's disconnect() can raise TimeoutError (and other errors) or hang;
            # a failed disconnect must never stop the caller from saving its progress.
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("%s: disconnect failed", self._device.address, exc_info=True)
