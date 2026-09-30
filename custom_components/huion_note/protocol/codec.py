"""Decode RETURN_OFFLINE_DATA (0x87) / retransmit (0x88) packets into strokes /
pages (protocol §4, §10).

Pure; mirrors the app's BluetoothUtil#decodePackagePoint, BluePoint and
BluetoothPackageData. Points are parsed PER PACKET (N = (len-5)//6, remainder incl.
checksum discarded). Coordinates map to the page origin top-left with no axis flip
(non-A4 device).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .frames import OrderCode

DEFAULT_MAX_X = 28200.0
DEFAULT_MAX_Y = 37400.0
DEFAULT_MAX_PRESS = 8191.0
FILTER_PRESS = 0.07  # below this share of MAX_PRESS a point counts as pen-up (app)


@dataclass
class StylusPoint:
    x: int
    y: int
    press: int
    pen_down: bool


@dataclass
class Limits:
    max_x: float = DEFAULT_MAX_X
    max_y: float = DEFAULT_MAX_Y
    max_press: float = DEFAULT_MAX_PRESS


@dataclass
class Page:
    index: int
    max_x: float
    max_y: float
    max_press: float
    strokes: list[list[StylusPoint]]
    complete: bool = True  # False if packets were still missing after retransmit
    # Every decoded point in stream order, including pen-up points and single-point
    # dots that points_to_strokes() drops. This is the lossless record of the page.
    points: list[StylusPoint] = field(default_factory=list)


def checksum_ok(pkt: bytes) -> bool:
    """A 0x87/0x88 packet's last byte is the sum of all other bytes & 0xff
    (BluetoothPackageData). Packets too short to carry a point never pass."""
    return len(pkt) >= 7 and sum(pkt[:-1]) & 0xFF == pkt[-1]


def _status_bits(max_press: float) -> int:
    """How many top bits of byte 5 are pen status; the rest extend the pressure.
    The split widens the pressure field for devices with a higher MAX_PRESS."""
    if 16384 < max_press <= 32768:
        return 1
    if 8192 < max_press <= 16384:
        return 2
    return 3


def decode_point(rec: bytes, max_press: float = DEFAULT_MAX_PRESS) -> StylusPoint:
    """Decode one 6-byte point record (mirrors BluePoint's constructor)."""
    x = rec[0] | (rec[1] << 8)
    y = rec[2] | (rec[3] << 8)
    shift = 8 - _status_bits(max_press)
    press = rec[4] | ((rec[5] & ((1 << shift) - 1)) << 8)
    status = rec[5] >> shift
    return StylusPoint(x=x, y=y, press=press, pen_down=status != 0)


def decode_packet(pkt: bytes, max_press: float = DEFAULT_MAX_PRESS) -> list[StylusPoint]:
    """Decode all points in one 0x87/0x88 packet (per-packet, remainder dropped)."""
    n = (len(pkt) - 5) // 6
    return [decode_point(pkt[5 + k * 6 : 5 + k * 6 + 6], max_press) for k in range(n)]


def packet_seq(pkt: bytes) -> int:
    """Sequence/index number at bytes 3..4 (u16 LE) — 0x87 seq or 0x88 index."""
    return pkt[3] | (pkt[4] << 8)


def parse_max_data(pkt: bytes) -> Limits:
    """Read MAX_X/MAX_Y/MAX_PRESS from a 0x95 packet, else defaults."""
    if len(pkt) < 11 or pkt[1] != OrderCode.MAX_DATA:
        return Limits()
    return Limits(
        max_x=float(pkt[5] << 16 | pkt[4] << 8 | pkt[3]),
        max_y=float(pkt[8] << 16 | pkt[7] << 8 | pkt[6]),
        max_press=float(pkt[10] << 8 | pkt[9]),
    )


def points_to_strokes(points: list, min_press: float = 0) -> list:
    """Split into strokes on pen-up (status 0, pressure 0 or below `min_press`).
    Drops 1-point strokes."""
    strokes, cur = [], []
    for p in points:
        if not p.pen_down or p.press == 0 or p.press < min_press:
            if len(cur) > 1:
                strokes.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) > 1:
        strokes.append(cur)
    return strokes


def decode_page(packets: list, limits: Limits, index: int = 0, complete: bool = True) -> Page:
    """Decode an ordered list of 0x87/0x88 packets into a Page."""
    pts = [p for pkt in packets for p in decode_packet(pkt, limits.max_press)]
    return Page(
        index=index,
        max_x=limits.max_x,
        max_y=limits.max_y,
        max_press=limits.max_press,
        strokes=points_to_strokes(pts, limits.max_press * FILTER_PRESS),
        complete=complete,
        points=pts,
    )
