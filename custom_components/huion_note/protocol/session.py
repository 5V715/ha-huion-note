"""Live multi-page sync orchestration (protocol §5, §10). Pure — talks to a
Transport (connect/send/recv/close); the integration supplies a bleak-backed one,
the tests a scripted fake.

Pages are found through ROM (stored-page count + last index; scanned downward
until every stored page arrived), falling back to scanning 0..CURRENT_PAGE. Empty
pages are skipped (indices of deleted pages stay empty), battery is read during
the handshake, and every page carries a `complete` flag. Commands are paced like
the official app's; retransmits go one at a time.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Protocol

from . import auth, codec, frames
from .errors import AuthFailed, PinRequired, TransportClosed
from .frames import OrderCode

_LOGGER = logging.getLogger(__name__)

MAX_REPORTED_PAGES = 1024  # sanity cap on the device's CURRENT_PAGE value
PAGE_TIMEOUT = 300.0       # seconds per page; ~4400 packets take <1 min even via a proxy
COMMAND_GAP = 0.35         # s between commands (app 2.2.3; bursts get answered out of order)
RETRANSMIT_TIMEOUT = 2.0   # s to wait for one re-requested packet (the app waits 1 s)
RETRANSMIT_ATTEMPTS = 5    # per packet, as in the app
GOODBYE_DELAY = 0.3        # s between DISCONNECT and closing the link (app 2.2.3)


class NoPageReply(TimeoutError):
    """The device didn't answer a page request (REQUEST_OFFLINE_DATA)."""


class Transport(Protocol):
    async def connect(self) -> None: ...
    async def send(self, frame: bytes) -> None: ...
    async def recv(self, timeout: Optional[float] = None) -> bytes: ...
    async def close(self) -> None: ...


class SyncSession:
    def __init__(self, transport: Transport, pin: Optional[str] = None,
                 idle_timeout: float = 5.0, max_pages: int = 64,
                 reply_timeout: float = 10.0,
                 max_reported_pages: int = MAX_REPORTED_PAGES,
                 page_timeout: float = PAGE_TIMEOUT,
                 retransmit_timeout: float = RETRANSMIT_TIMEOUT):
        self.t = transport
        self.pin = pin
        self.idle = idle_timeout
        self.max_pages = max_pages  # scan bound when the device doesn't report a count
        self.reply_timeout = reply_timeout
        self.max_reported_pages = max_reported_pages
        self.page_timeout = page_timeout
        self.retransmit_timeout = retransmit_timeout
        self._last_command = 0.0
        self._warned_truncated = False
        self.limits = codec.Limits()
        self.battery: Optional[int] = None

    async def run(self, on_page: Callable[[codec.Page], Awaitable[None]]) -> int:
        """Handshake, then fetch every non-empty page, awaiting `on_page` for each.
        Returns the number of pages delivered. The caller owns connect/close."""
        await self._authenticate()
        await self._command(frames.request_max_info())
        self.limits = codec.parse_max_data((await self._recv_op(OrderCode.MAX_DATA)).raw)
        self.battery = await self._query_battery()
        page_count = await self._current_page_count()
        rom = await self._query_rom()
        d1, d2 = frames.request_set_many_packet_distance()
        await self._command(d1)
        await self._command(d2)

        stored = None  # stop once this many pages arrived (ROM mode)
        if rom and 0 < rom[0] <= rom[1] + 1:
            stored, last_page = rom
            last_page = min(last_page, self.max_reported_pages)
            # Highest first: deleted pages leave empty indices at the bottom.
            order = range(last_page, -1, -1)
        else:
            if rom:
                _LOGGER.debug("ignoring implausible ROM reply (stored %d, last %d)", *rom)
            if page_count >= 1:
                # Trust the device's count (indices are logic pages; empty slots
                # persist, so it can exceed the number of pages with content). It's
                # unknown whether CURRENT_PAGE is a count or the highest index, so scan
                # 0..page_count inclusive — at worst one extra request, which ends the
                # scan below.
                if page_count > self.max_reported_pages:
                    _LOGGER.warning("device reports %d pages; scanning only the first %d",
                                    page_count, self.max_reported_pages)
                    page_count = self.max_reported_pages
                last_page = page_count
            else:
                last_page = self.max_pages - 1
            order = range(last_page + 1)
        delivered = 0
        for n, page in enumerate(order):
            try:
                # Per-page deadline: the stream/retransmit loops restart their idle
                # timer on any frame, so a chatty device could otherwise stall forever.
                async with asyncio.timeout(self.page_timeout):
                    count, packets, complete = await self._fetch_page(page)
            except NoPageReply:
                if n == 0:
                    raise
                log = _LOGGER.debug if n == len(order) - 1 else _LOGGER.warning
                log("no reply for page %d — treating it as the end of the notebook", page)
                break
            if count == 0:
                continue  # empty page — skip, keep scanning
            await on_page(codec.decode_page(packets, self.limits, page, complete))
            delivered += 1
            if stored is not None and delivered >= stored:
                break
        return delivered

    async def goodbye(self) -> None:
        """Tell the notebook we're leaving (DISCONNECT, like the app), then give it a
        moment before the caller closes the link. Never raises."""
        try:
            await self._command(frames.build_command(OrderCode.DISCONNECT))
            await asyncio.sleep(GOODBYE_DELAY)
        except Exception:  # noqa: BLE001 — a failed goodbye must not stop the close
            _LOGGER.debug("DISCONNECT not sent", exc_info=True)

    async def _command(self, frame: bytes) -> None:
        """Send a command at least COMMAND_GAP after the previous one."""
        loop = asyncio.get_running_loop()
        wait = self._last_command + COMMAND_GAP - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        await self.t.send(frame)
        self._last_command = loop.time()

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
        await self._command(frames.build_command(OrderCode.ELECTRICITY))
        r = await self._optional_reply(OrderCode.ELECTRICITY, 2.0)
        return r[3] if r and len(r) >= 4 else None

    async def _current_page_count(self) -> int:
        """Logic-page count from CURRENT_PAGE (0x85); 0 if unanswered."""
        await self._command(frames.build_command(OrderCode.CURRENT_PAGE))
        r = await self._optional_reply(OrderCode.CURRENT_PAGE, 3.0)
        return r[3] | (r[4] << 8) if r and len(r) >= 5 else 0

    async def _query_rom(self) -> Optional[tuple[int, int]]:
        """(stored pages, last page index) from ROM (0x8f); None if unanswered."""
        await self._command(frames.build_command(OrderCode.ROM))
        r = await self._optional_reply(OrderCode.ROM, 3.0)
        rom = frames.parse_rom(r) if r else None
        if rom:
            _LOGGER.debug("ROM: %d stored pages, last index %d", *rom)
        return rom

    async def _authenticate(self) -> None:
        # The device emits its challenge only after the client pokes it (protocol §6).
        await self._command(frames.build_command(OrderCode.VERIFY_CONNECT))
        ch = await self._recv_op(OrderCode.VERIFY_CONNECT)
        a, b, c = ch.raw[3], ch.raw[4], ch.raw[5]
        await self._command(auth.build_verify_result(a, b, c))
        status = (await self._recv_op(OrderCode.VERIFY_RESULT)).raw[3]
        if status == 2:
            if not self.pin:
                raise PinRequired("device requires a 6-digit PIN")
            f1, f2 = auth.build_verify_pwd_frames(self.pin)
            await self._command(f1)
            await self._command(f2)
            status = (await self._recv_op(OrderCode.VERIFY_RESULT)).raw[3]
        if status != 1:
            raise AuthFailed(f"auth rejected (status={status})")

    async def _fetch_page(self, page: int) -> tuple[int, list[bytes], bool]:
        """Download one page: (count, ordered_packets, complete). count 0 = empty."""
        await self._command(frames.request_page_data(page, 0))
        try:
            reply = await self._recv_op(OrderCode.REQUEST_OFFLINE_DATA)
        except asyncio.TimeoutError as err:
            raise NoPageReply(f"no reply to page {page} request") from err
        count = frames.parse_offline_count(reply.raw)
        if not count:
            return 0, [], True
        got: dict[int, bytes] = {}  # packets that passed the checksum
        bad: dict[int, bytes] = {}  # packets that failed it — used only as a last resort
        await self._drain_stream(got, bad, count)
        if not got and len(bad) >= 2:
            # Not one packet passed: this firmware computes the checksum some other
            # way. Don't re-fetch the whole page; trust the packets as before.
            _LOGGER.warning("page %d: no packet passed the checksum — not validating", page)
            got.update(bad)
            bad.clear()
        await self._fill_gaps(page, got, bad, count)
        missing = [s for s in range(1, count + 1) if s not in got]
        if missing:
            _LOGGER.warning("page %d incomplete: %d/%d packets; missing or corrupt %s",
                            page, len(got), count, missing[:20])
        packets = [got.get(s) or bad[s] for s in sorted(got.keys() | bad.keys())]
        return count, packets, not missing

    async def _drain_stream(self, got: dict, bad: dict, count: int) -> None:
        """Collect 0x87 packets until seq == count is seen, or idle/closed."""
        while True:
            try:
                value = await self.t.recv(timeout=self.idle)
            except (asyncio.TimeoutError, TransportClosed):
                return
            fr = frames.parse_huion_frame(value)
            if not fr or fr.op != OrderCode.RETURN_OFFLINE_DATA:
                continue
            self._check_length(fr.raw)
            seq = codec.packet_seq(fr.raw)
            if 1 <= seq <= count:
                _keep(got, bad, seq, fr.raw)
                if seq == count:
                    return

    def _check_length(self, raw: bytes) -> None:
        """Byte 2 of a page packet is its own length (0x7e = 126). Shorter means the
        link's MTU cut it — warn once per sync; the checksum rejects it anyway."""
        if len(raw) < raw[2] and not self._warned_truncated:
            self._warned_truncated = True
            _LOGGER.warning(
                "page packets arrive truncated (%d of %d bytes): the Bluetooth link's "
                "MTU is too small", len(raw), raw[2],
            )

    async def _fill_gaps(self, page: int, got: dict, bad: dict, count: int) -> None:
        """Re-request missing or corrupt packets via GET_PAGE_PACKAGE (0x88), one at
        a time like the app. If one gets no answer at all, the notebook isn't serving
        retransmits: stop rather than wait for every other packet as well."""
        for i in range(1, count + 1):
            answered = False
            for _ in range(RETRANSMIT_ATTEMPTS):
                if i in got:
                    break
                await self.t.send(frames.build_get_page_package(page, i))
                answered |= await self._await_packet(i, got, bad, count)
            if i not in got and not answered:
                return

    async def _await_packet(self, idx: int, got: dict, bad: dict, count: int) -> bool:
        """Collect packets until `idx` arrives (good or corrupt); False on timeout."""
        try:
            async with asyncio.timeout(self.retransmit_timeout):
                while True:
                    fr = frames.parse_huion_frame(await self.t.recv(timeout=self.idle))
                    if not fr or len(fr.raw) < 6 or fr.raw[2] != 0x7E or fr.op not in (
                            OrderCode.GET_PAGE_PACKAGE, OrderCode.RETURN_OFFLINE_DATA):
                        continue
                    self._check_length(fr.raw)
                    seq = codec.packet_seq(fr.raw)
                    if 1 <= seq <= count:
                        _keep(got, bad, seq, fr.raw)  # late stream packets count too
                        if seq == idx and fr.op == OrderCode.GET_PAGE_PACKAGE:
                            return True
        except (asyncio.TimeoutError, TransportClosed):
            return False

    # --- destructive: call only after the page is safely stored ---

    async def delete_page(self, page: int) -> bool:
        """Delete one stored page by index. True iff the device confirms."""
        await self._command(frames.build_delete_page(page))
        r = await self._optional_reply(OrderCode.DELETE_PAGE, self.reply_timeout)
        return bool(r) and len(r) > 3 and r[3] == 1

    async def clear_cache(self) -> bool:
        """Clear the device's offline cache (after deleting exported pages)."""
        await self._command(frames.build_clear_cache())
        r = await self._optional_reply(OrderCode.CLEAR_CACHE, self.reply_timeout)
        return bool(r) and len(r) > 3 and r[3] == 1


def _keep(got: dict, bad: dict, seq: int, raw: bytes) -> None:
    """File a packet as good or corrupt; a good copy always wins."""
    if codec.checksum_ok(raw):
        got[seq] = raw
        bad.pop(seq, None)
    elif seq not in got:
        bad[seq] = raw
