"""Pure protocol tests (no Home Assistant needed beyond import path)."""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.huion_note.pages import is_saved, page_digest, write_page
from custom_components.huion_note.protocol import auth, codec, frames, render
from custom_components.huion_note.protocol.errors import AuthFailed, PinRequired, TransportClosed
from custom_components.huion_note.protocol.frames import OrderCode
from custom_components.huion_note.protocol import session as session_mod
from custom_components.huion_note.protocol.session import SyncSession

from .conftest import FakeTablet, count, handshake, p87, rom, vr


async def run(session, pages):
    async def on_page(p):
        pages.append(p)
    return await session.run(on_page)


def test_verify_response_matches_capture():
    assert auth.build_verify_result(22, 122, 69).hex() == "cd820842fe3d00ed"


async def test_handshake_battery_and_pages():
    t = FakeTablet(handshake(bat=73, pages=1) + [count(2), p87(1), p87(2), count(1), p87(1)])
    s = SyncSession(t, idle_timeout=0.01)
    pages = []
    assert await run(s, pages) == 2
    assert s.battery == 73
    assert s.limits.max_x == 28200 and s.limits.max_y == 37400
    assert [p.index for p in pages] == [0, 1]
    assert all(p.complete for p in pages)
    assert t.sent[0] == frames.build_command(OrderCode.VERIFY_CONNECT)


async def test_empty_page_zero_is_skipped_not_terminal():
    # Some firmware keeps an empty page 0 with content after it.
    t = FakeTablet(handshake(pages=1) + [count(0), count(1), p87(1)])
    pages = []
    assert await run(SyncSession(t, idle_timeout=0.01), pages) == 1
    assert pages[0].index == 1


async def test_missing_packets_are_refetched():
    t = FakeTablet(handshake(pages=0) + [count(3), p87(1), p87(3)])
    s = SyncSession(t, idle_timeout=0.01, max_pages=1)
    pages = []
    await run(s, pages)
    assert [fr[5] for fr in t.ops(OrderCode.GET_PAGE_PACKAGE)] == [2]
    assert pages[0].complete


async def test_pin_required_and_rejected():
    t = FakeTablet([handshake()[0], vr(2)])
    with pytest.raises(PinRequired):
        await run(SyncSession(t), [])
    t = FakeTablet([handshake()[0], vr(2), vr(0)])
    with pytest.raises(AuthFailed):
        await run(SyncSession(t, pin="123456"), [])
    assert len(t.ops(OrderCode.VERIFY_PWD)) == 2


async def test_delete_page_confirmed():
    t = FakeTablet([])
    assert await SyncSession(t).delete_page(3)
    assert t.ops(OrderCode.DELETE_PAGE)[0].hex() == "cd8b080300" + "0000ed"


def _page(index=2):
    pts = [codec.StylusPoint(100 * i, 200 * i, 4000, True) for i in range(1, 5)]
    return codec.Page(index=index, max_x=28200.0, max_y=37400.0, max_press=8191.0,
                      strokes=[pts], points=pts + [codec.StylusPoint(500, 1000, 0, False)])


WHEN = datetime(2026, 9, 29, 8, 30, 0, tzinfo=timezone(timedelta(hours=2)))


def test_write_page_and_digest(tmp_path):
    page = _page()
    digest = page_digest(page)
    saved = write_page(page, str(tmp_path / "out"), WHEN, digest)
    # UTC timestamp + digest prefix
    assert os.path.basename(saved.base) == f"20260929T063000Z-page3-{digest[:8]}"
    assert is_saved(saved.base)
    data = json.loads(open(saved.json).read())
    assert data["strokes"][0][0] == {"x": 100, "y": 200, "press": 4000, "pen_down": True}
    # the lossless point list keeps pen-up points too
    assert data["points"][-1] == {"x": 500, "y": 1000, "press": 0, "pen_down": False}
    assert open(saved.png, "rb").read()[:4] == b"\x89PNG"
    # index-independent identity
    assert page_digest(_page(index=0)) == digest


def test_write_page_never_overwrites(tmp_path):
    page = _page()
    digest = page_digest(page)
    first = write_page(page, str(tmp_path), WHEN, digest)
    open(first.png, "ab").write(b"marker")
    second = write_page(page, str(tmp_path), WHEN, digest)  # same name would clash
    assert second.base == first.base + "-1"
    assert open(first.png, "rb").read().endswith(b"marker")
    assert is_saved(second.base)


def test_digest_covers_dots_and_pen_up_points():
    dot = lambda x: [codec.StylusPoint(x, 10, 50, True), codec.StylusPoint(x, 10, 0, False)]
    a = codec.decode_page([], codec.Limits(), 0)
    a.points = dot(100)
    b = codec.decode_page([], codec.Limits(), 1)
    b.points = dot(900)
    assert a.strokes == b.strokes == []  # the decoder drops single-point strokes
    assert page_digest(a) != page_digest(b)


def test_svg_output():
    # Same bytes the original huion_notes CLI renderer produced for this page.
    assert render.render_svg(_page()) == (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1190" '
        'style="background:#fff"><path d="M18.1,21.2 L21.2,27.4 L24.3,33.6 L27.3,39.8" '
        'fill="none" stroke="#111" stroke-width="2.5"/></svg>'
    )


async def test_trusts_page_count_beyond_64():
    """Review finding: pages past index 63 were never scanned."""
    script = handshake(pages=100) + [count(0)] * 70 + [count(1), p87(1)] + [count(0)] * 28 \
        + [count(1), p87(1)] + [count(0)]
    pages = []
    assert await run(SyncSession(FakeTablet(script), idle_timeout=0.01), pages) == 2
    assert [p.index for p in pages] == [70, 99]


async def test_no_reply_past_the_last_page_ends_the_scan():
    """CURRENT_PAGE may be a count, not the highest index: the extra request may go
    unanswered, and that must not fail a sync whose pages are all downloaded."""
    t = FakeTablet(handshake(pages=1) + [count(1), p87(1)])  # nothing for page 1
    pages = []
    assert await run(SyncSession(t, idle_timeout=0.01, reply_timeout=0.01), pages) == 1


async def test_no_reply_for_the_first_page_fails():
    t = FakeTablet(handshake(pages=1))
    with pytest.raises(TimeoutError):
        await run(SyncSession(t, idle_timeout=0.01, reply_timeout=0.01), [])


class ChattyTablet(FakeTablet):
    """After the script, answers every read with a heartbeat — never goes idle."""

    async def recv(self, timeout=None):
        if self._inbound:
            return await super().recv(timeout)
        await asyncio.sleep(0.001)
        return frames.heart_beat()


async def test_page_deadline_stops_a_device_that_never_goes_idle():
    t = ChattyTablet(handshake(pages=1) + [count(5), p87(1)])
    with pytest.raises(TimeoutError):
        await run(SyncSession(t, idle_timeout=0.05, page_timeout=0.3), [])


def test_packet_checksum():
    good, bad = p87(1), p87(1, bad=True)
    assert codec.checksum_ok(good)
    assert not codec.checksum_ok(bad)
    assert not codec.checksum_ok(good[:6])  # too short to carry a point


@pytest.mark.parametrize(("max_press", "b5", "press", "pen_down"), [
    (8191.0, 0x3F, 0x1F00, True),     # 3 status bits, 13-bit pressure (X10 today)
    (16383.0, 0x7F, 0x3F00, True),    # 2 status bits, 14-bit pressure
    (32767.0, 0x7F, 0x7F00, False),   # 1 status bit (clear), 15-bit pressure
    (32767.0, 0x80, 0x0000, True),    # 1 status bit (set)
])
def test_pressure_split_follows_max_press(max_press, b5, press, pen_down):
    """The app picks the status/pressure bit split in byte 5 from MAX_PRESS."""
    p = codec.decode_point(bytes([1, 0, 2, 0, 0x00, b5]), max_press)
    assert (p.press, p.pen_down) == (press, pen_down)


async def test_bad_checksum_packet_is_refetched():
    t = FakeTablet(handshake(pages=0) + [count(2), p87(1), p87(2, bad=True)])
    pages = []
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1), pages)
    assert [fr[5] for fr in t.ops(OrderCode.GET_PAGE_PACKAGE)] == [2]
    assert pages[0].complete


async def test_bad_packet_is_kept_but_page_incomplete_without_a_good_copy():
    t = FakeTablet(handshake(pages=0) + [count(2), p87(1), p87(2, bad=True)],
                   bad_retransmits=True)
    pages = []
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1), pages)
    assert not pages[0].complete  # never deleted from the notebook
    assert len(pages[0].points) == 4  # but no strokes are lost


async def test_every_packet_failing_checksum_is_not_treated_as_corruption():
    """If a firmware computes the checksum differently, don't re-fetch every packet."""
    t = FakeTablet(handshake(pages=0) + [count(2), p87(1, bad=True), p87(2, bad=True)])
    pages = []
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1), pages)
    assert not t.ops(OrderCode.GET_PAGE_PACKAGE)
    assert pages[0].complete and len(pages[0].points) == 4


async def test_commands_are_paced(monkeypatch):
    """The app leaves 350 ms between commands; back-to-back writes got answered out
    of order by the notebook."""
    monkeypatch.setattr(session_mod, "COMMAND_GAP", 0.05)
    t = FakeTablet(handshake(pages=0) + [count(1), p87(1)])
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1), [])
    gaps = [b - a for a, b in itertools.pairwise(t.sent_at)]
    assert min(gaps) >= 0.045


async def test_retransmits_go_one_at_a_time():
    t = FakeTablet(handshake(pages=0) + [count(4), p87(1), p87(4)])
    pages = []
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1), pages)
    ops = [fr for fr in t.sent if fr[1] == OrderCode.GET_PAGE_PACKAGE]
    assert [fr[5] for fr in ops] == [2, 3]
    # each request went out only after the previous packet had arrived
    sent = [f[1] for f in t.sent]
    assert sent[-2:] == [OrderCode.GET_PAGE_PACKAGE] * 2
    assert pages[0].complete


async def test_silent_retransmits_give_up_instead_of_stalling():
    t = FakeTablet(handshake(pages=0) + [count(4), p87(1)], retransmits=False)
    pages = []
    await run(SyncSession(t, idle_timeout=0.01, max_pages=1, retransmit_timeout=0.01), pages)
    idx = [fr[5] for fr in t.ops(OrderCode.GET_PAGE_PACKAGE)]
    assert set(idx) == {2}  # no reply for packet 2: don't ask for 3 and 4 as well
    assert not pages[0].complete


async def test_rom_window_scans_down_from_the_last_page():
    """ROM: 2 of 10 slots used, last page 7 — fetch 7, 6 and stop (pages below
    were deleted earlier and are empty)."""
    t = FakeTablet(handshake(pages=8) + [count(1), p87(1), count(1), p87(1, x=2)],
                   rom_reply=rom(free=8, total=10, last=7))
    pages = []
    assert await run(SyncSession(t, idle_timeout=0.01), pages) == 2
    assert [p.index for p in pages] == [7, 6]
    assert [fr[3] for fr in t.ops(OrderCode.REQUEST_OFFLINE_DATA)] == [7, 6]


async def test_rom_window_skips_holes():
    """Selective deletes leave holes; keep scanning down until all stored pages came."""
    t = FakeTablet(handshake(pages=8) + [count(1), p87(1), count(0), count(1), p87(1)],
                   rom_reply=rom(free=8, total=10, last=7))
    pages = []
    assert await run(SyncSession(t, idle_timeout=0.01), pages) == 2
    assert [p.index for p in pages] == [7, 5]


async def test_rom_flag_zero_means_the_last_slot_is_not_a_page():
    t = FakeTablet(handshake(pages=8) + [count(1), p87(1)],
                   rom_reply=rom(free=9, total=10, last=7, flag=0))
    pages = []
    await run(SyncSession(t, idle_timeout=0.01), pages)
    assert [p.index for p in pages] == [6]


@pytest.mark.parametrize("reply", [None, rom(free=10, total=10, last=3), rom(free=0, total=10, last=3)])
async def test_missing_or_implausible_rom_falls_back_to_the_page_count(reply):
    """No ROM, 0 stored pages, or more stored pages than slots up to `last`."""
    t = FakeTablet(handshake(pages=1) + [count(1), p87(1), count(0)], rom_reply=reply)
    pages = []
    assert await run(SyncSession(t, idle_timeout=0.01), pages) == 1
    assert [fr[3] for fr in t.ops(OrderCode.REQUEST_OFFLINE_DATA)] == [0, 1]


async def test_goodbye_sends_disconnect():
    t = FakeTablet([])
    await SyncSession(t).goodbye()
    assert t.ops(OrderCode.DISCONNECT) == [frames.build_command(OrderCode.DISCONNECT)]


async def test_goodbye_never_raises_on_a_closed_link():
    class Closed(FakeTablet):
        async def send(self, frame):
            raise TransportClosed("gone")
    await SyncSession(Closed([])).goodbye()


def test_faint_points_end_a_stroke_but_stay_in_the_page():
    """Below 7 % of MAX_PRESS the app treats a point as pen-up."""
    firm = lambda x: codec.StylusPoint(x, 0, 4000, True)
    faint = codec.StylusPoint(50, 0, 300, True)  # 300 < 0.07 * 8191
    pts = [firm(1), firm(2), faint, firm(3), firm(4)]
    assert codec.points_to_strokes(pts, min_press=573) == [pts[:2], pts[3:]]
    assert codec.points_to_strokes(pts) == [pts]  # no threshold: unchanged
    pkt_page = codec.decode_page([], codec.Limits(), 0)
    assert pkt_page.max_press == 8191.0
