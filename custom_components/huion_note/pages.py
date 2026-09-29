"""Write synced pages to disk and remember which ones we already have.

Pure (no Home Assistant imports) and synchronous — the coordinator runs it in the
executor. A page's identity is a hash of its strokes, so re-syncing a tablet that
still holds already-exported pages (delete-after-sync off) doesn't write duplicates,
while a page you kept writing on is saved again as a new version.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime

from .protocol import render
from .protocol.codec import Page


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
    """Content hash of the strokes (index-independent: indices shift on delete)."""
    pts = [[(p.x, p.y, p.press) for p in s] for s in page.strokes]
    return hashlib.sha256(json.dumps(pts, separators=(",", ":")).encode()).hexdigest()


def write_page(page: Page, out_dir: str, when: datetime) -> SavedPage:
    """Write <stamp>-page<N>.{svg,json,png}; SVG+JSON first, PNG last."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{when:%Y%m%d-%H%M%S}-page{page.index + 1}")
    _atomic_write(base + ".svg", render.render_svg(page).encode())
    _atomic_write(base + ".json", render.render_json(page).encode())
    _atomic_write(base + ".png", render.render_png(page))
    return SavedPage(base=base, digest=page_digest(page))


def is_saved(saved: SavedPage) -> bool:
    """Gate for deleting a page from the tablet: every file exists and is non-empty."""
    return all(
        os.path.isfile(p) and os.path.getsize(p) > 0
        for p in (saved.svg, saved.json, saved.png)
    )


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
