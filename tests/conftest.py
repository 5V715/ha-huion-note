"""Shared fixtures: a scripted fake tablet and HA custom-integration enablement."""
from __future__ import annotations

import asyncio
import time

import pytest

from custom_components.huion_note.protocol import session as session_mod

from custom_components.huion_note.protocol import frames
from custom_components.huion_note.protocol.errors import TransportClosed
from custom_components.huion_note.protocol.frames import OrderCode

_DOWN = bytes([0x10, 0x00, 0x10, 0x00, 0x05, 0x2F])
_DOWN2 = bytes([0x20, 0x00, 0x20, 0x00, 0x06, 0x2F])


def vc(a, b, c): return bytes([0xCD, OrderCode.VERIFY_CONNECT, 0x08, a, b, c, 0x00, 0xED])
def vr(status): return bytes([0xCD, OrderCode.VERIFY_RESULT, 0x08, status, 0, 0, 0, 0xED])
def maxd(): return bytes.fromhex("cd950b286e00189200ff1f")
def battery(pct): return bytes([0xCD, OrderCode.ELECTRICITY, 0x08, pct, 0, 0, 0, 0xED])
def cur_page(n): return bytes([0xCD, OrderCode.CURRENT_PAGE, 0x08, n & 0xFF, n >> 8, 0, 0, 0xED])
def count(n): return bytes([0xCD, OrderCode.REQUEST_OFFLINE_DATA, 0x05, n & 0xFF, (n >> 8) & 0xFF])
def del_ack(): return bytes([0xCD, OrderCode.DELETE_PAGE, 0x04, 0x01])


def rom(free, total, last, flag=1):
    """ROM reply: free/total page slots and the last page index (-1 if flag == 0)."""
    return bytes([0xCD, OrderCode.ROM, 0x08, free, total, last & 0xFF, last >> 8, flag])


def with_checksum(body, bad=False):
    """Append the packet checksum (sum of all bytes & 0xff); `bad` corrupts it."""
    return body + bytes([(sum(body) + (1 if bad else 0)) & 0xFF])


def p87(seq, x=0, bad=False):
    pts = bytes([0x10 + x, 0x00, 0x10, 0x00, 0x05, 0x2F]) + _DOWN2
    return with_checksum(bytes([0xCD, 0x87, 0x7E, seq & 0xFF, (seq >> 8) & 0xFF]) + pts, bad)


def p88(idx, bad=False):
    return with_checksum(
        bytes([0xCD, 0x88, 0x7E, idx & 0xFF, (idx >> 8) & 0xFF]) + _DOWN + _DOWN2, bad)


def handshake(bat=80, pages=None):
    out = [vc(22, 122, 69), vr(1), maxd(), battery(bat)]
    if pages is not None:
        out.append(cur_page(pages))
    return out


@pytest.fixture(autouse=True)
def no_command_pacing(monkeypatch):
    """The real 350 ms command gap and 300 ms goodbye wait would only slow tests down."""
    monkeypatch.setattr(session_mod, "COMMAND_GAP", 0.0)
    monkeypatch.setattr(session_mod, "GOODBYE_DELAY", 0.0)


_NO_REPLY = object()


class FakeTablet:
    """Transport double. Replies are scripted; DELETE_PAGE is auto-acked,
    GET_PAGE_PACKAGE auto-answered (unless `retransmits=False`) and ROM answered
    with `rom_reply` (or not at all). recv() on an empty script idles (TimeoutError)."""

    def __init__(self, inbound, bad_retransmits=False, retransmits=True, rom_reply=None):
        self._inbound = list(inbound)
        self.bad_retransmits = bad_retransmits
        self.retransmits = retransmits
        self.rom_reply = rom_reply
        self.sent: list[bytes] = []
        self.sent_at: list[float] = []
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def send(self, frame):
        self.sent.append(frame)
        self.sent_at.append(time.monotonic())
        fr = frames.parse_huion_frame(frame)
        if fr and fr.op == OrderCode.ROM:
            # answered before anything else still queued; no reply = one idle timeout
            self._inbound.insert(0, self.rom_reply or _NO_REPLY)
        if fr and fr.op == OrderCode.GET_PAGE_PACKAGE and self.retransmits:
            self._inbound.append(p88(fr.raw[5] | (fr.raw[6] << 8), bad=self.bad_retransmits))
        if fr and fr.op == OrderCode.DELETE_PAGE:
            self._inbound.append(del_ack())

    async def recv(self, timeout=None):
        if self._inbound:
            item = self._inbound.pop(0)
            if item is _NO_REPLY:
                await asyncio.sleep(0)
                raise asyncio.TimeoutError()
            return item
        if self.closed:
            raise TransportClosed()
        await asyncio.sleep(0)
        raise asyncio.TimeoutError()

    async def close(self):
        self.closed = True

    def ops(self, op):
        return [b for b in self.sent if (fr := frames.parse_huion_frame(b)) and fr.op == op]
