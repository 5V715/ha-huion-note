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

_EXTS = (".svg", ".json", ".png", ".pdf")
# What must be on disk before the tablet's copy may be deleted. The PDF is written
# too, but pages saved before PDFs existed don't have one and needn't be re-saved.
_REQUIRED = (".svg", ".json", ".png")


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

    @property
    def pdf(self) -> str:
        return self.base + ".pdf"


def media_content_id(path: str, media_dirs: dict[str, str]) -> str:
    """The media-source link for a file inside one of Home Assistant's media folders
    (`media_dirs`: source name -> folder; the most specific folder wins), or "" if
    the file is in none of them. AI Tasks and the media browser need this link."""
    real = os.path.realpath(path)
    best: tuple[int, str, str] | None = None
    for name, folder in media_dirs.items():
        root = os.path.realpath(folder)
        if os.path.commonpath([real, root]) == root and (not best or len(root) > best[0]):
            best = (len(root), name, os.path.relpath(real, root))
    return f"media-source://media_source/{best[1]}/{best[2]}" if best else ""


def page_digest(page: Page) -> str:
    """Content hash of every decoded point (index-independent: indices shift on delete)."""
    h = hashlib.sha256()
    for p in page.points:
        h.update(struct.pack("<HHH?", p.x, p.y, p.press, p.pen_down))
    return h.hexdigest()


def write_page(
    page: Page,
    out_dir: str,
    when: datetime,
    digest: str,
    line_width_mm: float = render.DEFAULT_LINE_WIDTH_MM,
) -> SavedPage:
    """Write <UTC stamp>-page<N>-<digest8>.{svg,json,pdf,png} without overwriting anything."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = os.path.join(out_dir, f"{stamp}-page{page.index + 1}-{digest[:8]}")
    base, n = stem, 1
    while any(os.path.lexists(base + ext) for ext in _EXTS):
        base, n = f"{stem}-{n}", n + 1
    _write_new(base + ".svg", render.render_svg(page, full_width_mm=line_width_mm).encode())
    _write_new(base + ".json", render.render_json(page).encode())
    _write_new(base + ".pdf", render.render_pdf(page, full_width_mm=line_width_mm))
    _write_new(base + ".png", render.render_png(page, full_width_mm=line_width_mm))
    _fsync_dir(out_dir)
    return SavedPage(base=base, digest=digest)


def is_saved(base: str) -> bool:
    """Gate for deleting a page from the tablet: every file exists and is non-empty."""
    return all(
        os.path.isfile(base + ext) and os.path.getsize(base + ext) > 0 for ext in _REQUIRED
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
