"""BleakTransport pairing behaviour. The notebook only keeps a connection from a
paired central: unpaired links are dropped at once, and a pairing the notebook has
forgotten (e.g. after it paired with a phone) makes every connect time out."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakNotFoundError

from custom_components.huion_note.protocol.errors import TransportClosed
from custom_components.huion_note.transport import BleakTransport

DEVICE = BLEDevice("AA:BB:CC:DD:EE:FF", "Huion Note-X10", {"path": "/org/bluez/hci0/dev_AA"})
TIMEOUT = BleakNotFoundError("Failed to connect after 4 attempt(s): TimeoutError")


def fake_client():
    client = MagicMock()
    client.mtu_size = 247
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.disconnect = AsyncMock()
    return client


def patched(connect_side_effect, paired=False):
    connect = AsyncMock(side_effect=connect_side_effect)
    remove = AsyncMock(return_value=True)
    return connect, remove, (
        patch("custom_components.huion_note.transport.establish_connection", connect),
        patch("custom_components.huion_note.transport.bluez.is_paired",
              AsyncMock(return_value=paired)),
        patch("custom_components.huion_note.transport.bluez.remove_pairing", remove),
    )


async def test_connect_asks_to_pair():
    connect, _, patches = patched([fake_client()])
    with patches[0], patches[1], patches[2]:
        t = BleakTransport(DEVICE)
        await t.connect()
        await t.close()
    assert connect.call_args.kwargs["pair"] is True


async def test_stale_pairing_is_removed_and_connect_retried():
    connect, remove, patches = patched([TIMEOUT, fake_client()], paired=True)
    with patches[0], patches[1], patches[2]:
        t = BleakTransport(DEVICE)
        await t.connect()
        await t.close()
    remove.assert_awaited_once_with(DEVICE)
    assert connect.await_count == 2
    assert connect.call_args.kwargs["pair"] is True


async def test_timeout_without_a_pairing_is_not_retried():
    connect, remove, patches = patched([TIMEOUT], paired=False)
    with patches[0], patches[1], patches[2], pytest.raises(TransportClosed, match="connect failed"):
        await BleakTransport(DEVICE).connect()
    remove.assert_not_awaited()
    assert connect.await_count == 1


async def test_proxy_without_pairing_support_connects_unpaired():
    """ESPHome proxies before 2024.3 raise NotImplementedError for pairing."""
    connect, _, patches = patched([NotImplementedError("Pairing is not available"), fake_client()])
    with patches[0], patches[1], patches[2]:
        t = BleakTransport(DEVICE)
        await t.connect()
        await t.close()
    assert connect.call_args_list[-1].kwargs["pair"] is False


async def test_dropped_link_says_the_notebook_closed_it():
    connect, _, patches = patched([fake_client()])
    with patches[0], patches[1], patches[2]:
        t = BleakTransport(DEVICE)
        await t.connect()
        t._on_disconnect(None)  # the notebook hangs up
        with pytest.raises(TransportClosed, match="notebook closed the connection"):
            await t.send(b"\xcd\x81\x08\x00\x00\x00\x00\xed")
        with pytest.raises(TransportClosed, match="notebook closed the connection"):
            await t.recv(timeout=0.1)
        await t.close()


async def test_disconnect_from_a_failed_attempt_does_not_close_the_new_link():
    """establish_connection may report a disconnect for an attempt that failed;
    the connection that finally succeeds must still be usable."""
    client = fake_client()

    async def connect(*args, **kwargs):
        if connect.calls == 0:
            connect.calls += 1
            kwargs["disconnected_callback"](None)  # the failed attempt's link drops
            raise TIMEOUT
        return client
    connect.calls = 0

    with patch("custom_components.huion_note.transport.establish_connection", connect), \
            patch("custom_components.huion_note.transport.bluez.is_paired",
                  AsyncMock(return_value=True)), \
            patch("custom_components.huion_note.transport.bluez.remove_pairing",
                  AsyncMock(return_value=True)):
        t = BleakTransport(DEVICE)
        await t.connect()
        await t.send(b"\xcd\x81\x08\x00\x00\x00\x00\xed")
        await t.close()
    client.write_gatt_char.assert_awaited_once()


async def test_connect_does_not_ask_bluez_for_the_mtu():
    """bleak's BlueZ backend only reports a placeholder MTU of 23 (with a
    UserWarning) unless it was acquired first: reading it caused false alarms."""
    client = fake_client()
    type(client).mtu_size = PropertyMock(side_effect=AssertionError("mtu_size read"))
    connect, _, patches = patched([client])
    with patches[0], patches[1], patches[2]:
        t = BleakTransport(DEVICE)
        await t.connect()
        await t.close()
