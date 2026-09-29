"""bleak-backed Transport for protocol.session.SyncSession.

Works with a local BlueZ adapter or an ESPHome Bluetooth proxy — whatever Home
Assistant's bluetooth integration hands us. Notifications from FFE1 (data) and
indications from FFE2 (commands) are funnelled into one queue; the session
dispatches by opcode, so it doesn't care which characteristic a frame came from.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import CMD_CHAR_UUID, DATA_CHAR_UUID
from .protocol.errors import TransportClosed
from .protocol.frames import heart_beat

_LOGGER = logging.getLogger(__name__)

# 126-byte data values need ATT_MTU >= 129.
MIN_MTU = 129


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
        self._ka_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self._device,
                self._device.name or self._device.address,
                disconnected_callback=self._on_disconnect,
                ble_device_callback=self._device_cb,
            )
            mtu = self._client.mtu_size
            if mtu < MIN_MTU:
                _LOGGER.warning(
                    "%s: negotiated MTU %s < %s; page packets may be truncated",
                    self._device.address, mtu, MIN_MTU,
                )
            await self._client.start_notify(DATA_CHAR_UUID, self._on_value)
            await self._client.start_notify(CMD_CHAR_UUID, self._on_value)
        except (BleakError, asyncio.TimeoutError) as err:
            await self.close()
            raise TransportClosed(f"connect failed: {err}") from err
        self._ka_task = asyncio.create_task(self._keepalive_loop())

    def _on_value(self, _char, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    def _on_disconnect(self, _client) -> None:
        _LOGGER.debug("%s: disconnected", self._device.address)
        self._closed = True
        self._queue.put_nowait(None)  # wake any pending recv()

    async def send(self, frame: bytes) -> None:
        if self._closed or not self._client:
            raise TransportClosed("not connected")
        try:
            await self._client.write_gatt_char(CMD_CHAR_UUID, frame, response=False)
        except BleakError as err:
            raise TransportClosed(f"write failed: {err}") from err

    async def recv(self, timeout: Optional[float] = None) -> bytes:
        item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        if item is None:
            self._queue.put_nowait(None)  # stay closed for later readers
            raise TransportClosed("device disconnected")
        return item

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
            try:
                await client.disconnect()
            except BleakError:
                pass
