"""Pure protocol tests (no Home Assistant needed beyond import path)."""
from __future__ import annotations

import json

import pytest

from custom_components.huion_note.pages import is_saved, page_digest, write_page
from custom_components.huion_note.protocol import auth, codec, frames, render
from custom_components.huion_note.protocol.errors import AuthFailed, PinRequired
from custom_components.huion_note.protocol.frames import OrderCode
from custom_components.huion_note.protocol.session import SyncSession

from .conftest import FakeTablet, count, handshake, p87, vr


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


def _page():
    pts = [codec.StylusPoint(100 * i, 200 * i, 4000, True) for i in range(1, 5)]
    return codec.Page(index=2, max_x=28200.0, max_y=37400.0, max_press=8191.0, strokes=[pts])


def test_write_page_and_digest(tmp_path):
    from datetime import datetime

    page = _page()
    saved = write_page(page, str(tmp_path / "out"), datetime(2026, 9, 29, 8, 30, 0))
    assert saved.base.endswith("20260929-083000-page3")
    assert is_saved(saved)
    assert json.loads(open(saved.json).read())["strokes"][0][0] == {
        "x": 100, "y": 200, "press": 4000, "pen_down": True}
    assert open(saved.png, "rb").read()[:4] == b"\x89PNG"
    # index-independent identity
    other = _page()
    other.index = 0
    assert page_digest(other) == saved.digest


def test_svg_output():
    # Same bytes the original huion_notes CLI renderer produced for this page.
    assert render.render_svg(_page()) == (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1190" '
        'style="background:#fff"><path d="M18.1,21.2 L21.2,27.4 L24.3,33.6 L27.3,39.8" '
        'fill="none" stroke="#111" stroke-width="2.5"/></svg>'
    )
