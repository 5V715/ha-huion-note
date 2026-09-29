"""Huion device framing: `cd <op> <len> ... ed`, OrderCode constants, command
builders and response parsers (protocol §2, §3, §7, §10). Pure and device-free."""
from __future__ import annotations

from dataclasses import dataclass

START = 0xCD
END = 0xED


class OrderCode:
    """Opcode constants (from the app's OrderCode.java; see protocol §3, §10)."""
    HEART_BEAT = 0x80
    VERIFY_CONNECT = 0x81
    VERIFY_RESULT = 0x82
    VERIFY_PWD = 0x83
    MODE = 0x84
    CURRENT_PAGE = 0x85
    REQUEST_OFFLINE_DATA = 0x86
    RETURN_OFFLINE_DATA = 0x87
    GET_PAGE_PACKAGE = 0x88     # also the retransmit channel (§10)
    NEXT_PAGE = 0x8A            # device -> host "page created" notice
    DELETE_PAGE = 0x8B          # destructive — sent only after a verified export
    CLEAR_CACHE = 0x8C          # destructive
    ELECTRICITY = 0x8E          # battery %, reply byte [3]
    DEVICE_NAME = 0x91
    GET_PWD = 0x93
    MAX_DATA = 0x95
    SET_MANY_PACKET_DISTANCE = 0x96
    VERSION = 0xC9


@dataclass
class HuionFrame:
    op: int
    length: int
    payload: bytes  # bytes after the length byte (best-effort; not length-trimmed)
    raw: bytes      # the full characteristic value (cd ... ed/checksum)


def parse_huion_frame(value: bytes) -> "HuionFrame | None":
    """Parse a characteristic value into a HuionFrame, or None if not a frame."""
    if len(value) < 3 or value[0] != START:
        return None
    return HuionFrame(op=value[1], length=value[2], payload=bytes(value[3:]), raw=bytes(value))


def build_command(op: int, a: int = 0, b: int = 0, c: int = 0, d: int = 0) -> bytes:
    """Build a fixed 8-byte command frame: cd <op> 08 a b c d ed (protocol §7)."""
    return bytes([START, op, 0x08, a & 0xFF, b & 0xFF, c & 0xFF, d & 0xFF, END])


def request_max_info() -> bytes:
    return build_command(OrderCode.MAX_DATA)


def request_set_many_packet_distance() -> tuple[bytes, bytes]:
    return (
        build_command(OrderCode.SET_MANY_PACKET_DISTANCE, 1, 3, 0, 0),
        build_command(OrderCode.SET_MANY_PACKET_DISTANCE, 3, 2, 0, 0),
    )


def request_page_data(page: int = 0, sub: int = 0) -> bytes:
    """REQUEST_OFFLINE_DATA: cd 86 08 <page_lo> <page_hi> <sub> 00 ed."""
    return build_command(
        OrderCode.REQUEST_OFFLINE_DATA, page & 0xFF, (page >> 8) & 0xFF, sub & 0xFF, 0
    )


def build_get_page_package(page: int, idx: int) -> bytes:
    """GET_PAGE_PACKAGE — retransmit one packet by index (§10):
    cd 88 08 <page_lo> <page_hi> <idx_lo> <idx_hi> ed."""
    return bytes([
        START, OrderCode.GET_PAGE_PACKAGE, 0x08,
        page & 0xFF, (page >> 8) & 0xFF, idx & 0xFF, (idx >> 8) & 0xFF, END,
    ])


def build_delete_page(page: int) -> bytes:
    """DELETE_PAGE: cd 8b 08 <page_lo> <page_hi> 00 00 ed. DESTRUCTIVE."""
    return bytes([
        START, OrderCode.DELETE_PAGE, 0x08,
        page & 0xFF, (page >> 8) & 0xFF, 0x00, 0x00, END,
    ])


def build_clear_cache() -> bytes:
    """CLEAR_CACHE: cd 8c 08 00 00 00 00 ed. DESTRUCTIVE."""
    return build_command(OrderCode.CLEAR_CACHE)


def parse_offline_count(value: bytes) -> "int | None":
    """Parse a REQUEST_OFFLINE_DATA response `cd 86 05 <count_lo> <count_hi>` ->
    packet count (u16 LE). Returns None if not such a frame."""
    if (len(value) >= 5 and value[0] == START
            and value[1] == OrderCode.REQUEST_OFFLINE_DATA and value[2] == 0x05):
        return value[3] | (value[4] << 8)
    return None


def heart_beat() -> bytes:
    return build_command(OrderCode.HEART_BEAT)
