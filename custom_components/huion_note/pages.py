"""Write synced pages to disk and remember which ones we already have.

Pure (no Home Assistant imports) and synchronous — the coordinator runs it in the
executor. A page's identity is a hash of every decoded point (pen-up points and
dots included), so re-syncing a tablet that still holds already-exported pages
doesn't write duplicates, while a page you kept writing on is saved again as a
new version.

Files are never overwritten: names carry a UTC timestamp and a digest prefix,
and each file is created exclusively (mode "x"), which fails if the name exists.
The files and their directory are fsynced before the page counts as saved,
because "saved" is what allows deleting the tablet's copy.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from .protocol import render
from .protocol.codec import Page

_EXTS = (".svg", ".json", ".png")


@dataclass
class SavedPage:
    base: str      # path without extension
    digest: str

    @property
    def png(self) -> str:
        return self.base + ".png"

    @property
    def svg(self) -> str:
        return self.base + ".svg"

    @property
    def json(self) -> str:
        return self.base + ".json"


def page_digest(page: Page) -> str:
    """Content hash of every decoded point (index-independent: indices shift on delete)."""
    h = hashlib.sha256()
    for p in page.points:
        h.update(struct.pack("<HHH?", p.x, p.y, p.press, p.pen_down))
    return h.hexdigest()


def write_page(page: Page, out_dir: str, when: datetime, digest: str) -> SavedPage:
    """Write <UTC stamp>-page<N>-<digest8>.{svg,json,png} without overwriting anything."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = os.path.join(out_dir, f"{stamp}-page{page.index + 1}-{digest[:8]}")
    base, n = stem, 1
    while any(os.path.lexists(base + ext) for ext in _EXTS):
        base, n = f"{stem}-{n}", n + 1
    _write_new(base + ".svg", render.render_svg(page).encode())
    _write_new(base + ".json", render.render_json(page).encode())
    _write_new(base + ".png", render.render_png(page))
    _fsync_dir(out_dir)
    return SavedPage(base=base, digest=digest)


def is_saved(base: str) -> bool:
    """Gate for deleting a page from the tablet: every file exists and is non-empty."""
    return all(
        os.path.isfile(base + ext) and os.path.getsize(base + ext) > 0 for ext in _EXTS
    )


def _write_new(path: str, data: bytes) -> None:
    """Write `path` durably; raises FileExistsError rather than replacing a file."""
    with open(path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_dir(path: str) -> None:
    """Persist the new directory entries. Best effort: some filesystems (SMB,
    FAT) can't fsync a directory; the file data itself is already fsynced."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
