"""Shared fixtures: a scripted fake tablet and HA custom-integration enablement."""
from __future__ import annotations

import asyncio

import pytest

from custom_components.huion_note.protocol import frames
from custom_components.huion_note.protocol.errors import TransportClosed
from custom_components.huion_note.protocol.frames import OrderCode

_DOWN = bytes([0x10, 0x00, 0x10, 0x00, 0x05, 0x20])
_DOWN2 = bytes([0x20, 0x00, 0x20, 0x00, 0x06, 0x20])


def vc(a, b, c): return bytes([0xCD, OrderCode.VERIFY_CONNECT, 0x08, a, b, c, 0x00, 0xED])
def vr(status): return bytes([0xCD, OrderCode.VERIFY_RESULT, 0x08, status, 0, 0, 0, 0xED])
def maxd(): return bytes.fromhex("cd950b286e00189200ff1f")
def battery(pct): return bytes([0xCD, OrderCode.ELECTRICITY, 0x08, pct, 0, 0, 0, 0xED])
def cur_page(n): return bytes([0xCD, OrderCode.CURRENT_PAGE, 0x08, n & 0xFF, n >> 8, 0, 0, 0xED])
def count(n): return bytes([0xCD, OrderCode.REQUEST_OFFLINE_DATA, 0x05, n & 0xFF, (n >> 8) & 0xFF])
def del_ack(): return bytes([0xCD, OrderCode.DELETE_PAGE, 0x04, 0x01])


def p87(seq, x=0):
    pts = bytes([0x10 + x, 0x00, 0x10, 0x00, 0x05, 0x20]) + _DOWN2
    return bytes([0xCD, 0x87, 0x7E, seq & 0xFF, (seq >> 8) & 0xFF]) + pts + bytes([0xEE])


def p88(idx):
    return bytes([0xCD, 0x88, 0x7E, idx & 0xFF, (idx >> 8) & 0xFF]) + _DOWN + _DOWN2 + bytes([0xEE])


def handshake(bat=80, pages=None):
    out = [vc(22, 122, 69), vr(1), maxd(), battery(bat)]
    if pages is not None:
        out.append(cur_page(pages))
    return out


class FakeTablet:
    """Transport double. Replies are scripted; DELETE_PAGE is auto-acked and
    GET_PAGE_PACKAGE auto-answered. recv() on an empty script idles (TimeoutError)."""

    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent: list[bytes] = []
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def send(self, frame):
        self.sent.append(frame)
        fr = frames.parse_huion_frame(frame)
        if fr and fr.op == OrderCode.GET_PAGE_PACKAGE:
            self._inbound.append(p88(fr.raw[5] | (fr.raw[6] << 8)))
        if fr and fr.op == OrderCode.DELETE_PAGE:
            self._inbound.append(del_ack())

    async def recv(self, timeout=None):
        if self._inbound:
            return self._inbound.pop(0)
        if self.closed:
            raise TransportClosed()
        await asyncio.sleep(0)
        raise asyncio.TimeoutError()

    async def close(self):
        self.closed = True

    def ops(self, op):
        return [b for b in self.sent if (fr := frames.parse_huion_frame(b)) and fr.op == op]
