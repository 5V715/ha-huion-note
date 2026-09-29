"""Live multi-page sync orchestration (protocol §5, §10). Pure — talks to a
Transport (connect/send/recv/close); the integration supplies a bleak-backed one,
the tests a scripted fake.

The device's CURRENT_PAGE count bounds the scan and empty pages are skipped
(some firmware keeps an empty page 0 with content after it), battery is read
during the handshake, and every page carries a `complete` flag.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Protocol

from . import auth, codec, frames
from .errors import AuthFailed, PinRequired, TransportClosed
from .frames import OrderCode

_LOGGER = logging.getLogger(__name__)


class Transport(Protocol):
    async def connect(self) -> None: ...
    async def send(self, frame: bytes) -> None: ...
    async def recv(self, timeout: Optional[float] = None) -> bytes: ...
    async def close(self) -> None: ...


class SyncSession:
    def __init__(self, transport: Transport, pin: Optional[str] = None,
                 idle_timeout: float = 5.0, max_pages: int = 64,
                 reply_timeout: float = 10.0):
        self.t = transport
        self.pin = pin
        self.idle = idle_timeout
        self.max_pages = max_pages
        self.reply_timeout = reply_timeout
        self.limits = codec.Limits()
        self.battery: Optional[int] = None

    async def run(self, on_page: Callable[[codec.Page], Awaitable[None]]) -> int:
        """Handshake, then fetch every non-empty page, awaiting `on_page` for each.
        Returns the number of pages delivered. The caller owns connect/close."""
        await self._authenticate()
        await self.t.send(frames.request_max_info())
        self.limits = codec.parse_max_data((await self._recv_op(OrderCode.MAX_DATA)).raw)
        self.battery = await self._query_battery()
        page_count = await self._current_page_count()
        d1, d2 = frames.request_set_many_packet_distance()
        await self.t.send(d1)
        await self.t.send(d2)

        last_page = page_count if 1 <= page_count <= self.max_pages else self.max_pages - 1
        delivered = 0
        for page in range(last_page + 1):
            count, packets, complete = await self._fetch_page(page)
            if count == 0:
                continue  # empty page — skip, keep scanning
            await on_page(codec.decode_page(packets, self.limits, page, complete))
            delivered += 1
        return delivered

    async def _recv_op(self, op: int, timeout: Optional[float] = None) -> frames.HuionFrame:
        """Read frames until one has opcode `op`, ignoring others (heartbeats, etc.).
        Bounded by an overall deadline so a chatty device can't hang the sync."""
        timeout = timeout or self.reply_timeout

        async def _await_op() -> frames.HuionFrame:
            while True:
                fr = frames.parse_huion_frame(await self.t.recv(timeout=timeout))
                if fr and fr.op == op:
                    return fr
        return await asyncio.wait_for(_await_op(), timeout=timeout * 3)

    async def _optional_reply(self, op: int, timeout: float) -> Optional[bytes]:
        try:
            return (await self._recv_op(op, timeout=timeout)).raw
        except (asyncio.TimeoutError, TransportClosed):
            return None

    async def _query_battery(self) -> Optional[int]:
        """Battery % from ELECTRICITY (0x8e), reply byte [3]; None if unanswered."""
        await self.t.send(frames.build_command(OrderCode.ELECTRICITY))
        r = await self._optional_reply(OrderCode.ELECTRICITY, 2.0)
        return r[3] if r and len(r) >= 4 else None

    async def _current_page_count(self) -> int:
        """Logic-page count from CURRENT_PAGE (0x85); 0 if unanswered."""
        await self.t.send(frames.build_command(OrderCode.CURRENT_PAGE))
        r = await self._optional_reply(OrderCode.CURRENT_PAGE, 3.0)
        return r[3] | (r[4] << 8) if r and len(r) >= 5 else 0

    async def _authenticate(self) -> None:
        # The device emits its challenge only after the client pokes it (protocol §6).
        await self.t.send(frames.build_command(OrderCode.VERIFY_CONNECT))
        ch = await self._recv_op(OrderCode.VERIFY_CONNECT)
        a, b, c = ch.raw[3], ch.raw[4], ch.raw[5]
        await self.t.send(auth.build_verify_result(a, b, c))
        status = (await self._recv_op(OrderCode.VERIFY_RESULT)).raw[3]
        if status == 2:
            if not self.pin:
                raise PinRequired("device requires a 6-digit PIN")
            f1, f2 = auth.build_verify_pwd_frames(self.pin)
            await self.t.send(f1)
            await self.t.send(f2)
            status = (await self._recv_op(OrderCode.VERIFY_RESULT)).raw[3]
        if status != 1:
            raise AuthFailed(f"auth rejected (status={status})")

    async def _fetch_page(self, page: int) -> tuple[int, list[bytes], bool]:
        """Download one page: (count, ordered_packets, complete). count 0 = empty."""
        await self.t.send(frames.request_page_data(page, 0))
        count = frames.parse_offline_count(
            (await self._recv_op(OrderCode.REQUEST_OFFLINE_DATA)).raw)
        if not count:
            return 0, [], True
        got: dict[int, bytes] = {}
        await self._drain_stream(got, count)
        await self._fill_gaps(page, got, count)
        missing = [s for s in range(1, count + 1) if s not in got]
        if missing:
            _LOGGER.warning("page %d incomplete: %d/%d packets; missing %s",
                            page, len(got), count, missing[:20])
        return count, [got[s] for s in sorted(got)], not missing

    async def _drain_stream(self, got: dict, count: int) -> None:
        """Collect 0x87 packets until seq == count is seen, or idle/closed."""
        while True:
            try:
                value = await self.t.recv(timeout=self.idle)
            except (asyncio.TimeoutError, TransportClosed):
                return
            fr = frames.parse_huion_frame(value)
            if not fr or fr.op != OrderCode.RETURN_OFFLINE_DATA:
                continue
            seq = codec.packet_seq(fr.raw)
            if 1 <= seq <= count:
                got[seq] = fr.raw
                if seq == count:
                    return

    async def _fill_gaps(self, page: int, got: dict, count: int, max_rounds: int = 5) -> None:
        """Re-request missing packets via GET_PAGE_PACKAGE (0x88); collect replies."""
        for _ in range(max_rounds):
            missing = [s for s in range(1, count + 1) if s not in got]
            if not missing:
                return
            for i in missing:
                await self.t.send(frames.build_get_page_package(page, i))
            while True:
                try:
                    value = await self.t.recv(timeout=self.idle)
                except (asyncio.TimeoutError, TransportClosed):
                    break
                fr = frames.parse_huion_frame(value)
                if (fr and fr.op == OrderCode.GET_PAGE_PACKAGE
                        and len(fr.raw) >= 6 and fr.raw[2] == 0x7E):
                    idx = codec.packet_seq(fr.raw)
                    if 1 <= idx <= count:
                        got[idx] = fr.raw
                        if len(got) == count:
                            break

    # --- destructive: call only after the page is safely stored ---

    async def delete_page(self, page: int) -> bool:
        """Delete one stored page by index. True iff the device confirms."""
        await self.t.send(frames.build_delete_page(page))
        r = await self._optional_reply(OrderCode.DELETE_PAGE, self.reply_timeout)
        return bool(r) and len(r) > 3 and r[3] == 1

    async def clear_cache(self) -> bool:
        """Clear the device's offline cache (after deleting exported pages)."""
        await self.t.send(frames.build_clear_cache())
        r = await self._optional_reply(OrderCode.CLEAR_CACHE, self.reply_timeout)
        return bool(r) and len(r) > 3 and r[3] == 1
