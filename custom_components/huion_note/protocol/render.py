"""Render decoded pages to SVG (vector master), JSON (ordered lossless points —
input for handwriting recognition) and PNG (Pillow; same look as the Android app).
"""
from __future__ import annotations

import io
import json

from .codec import Page

WIDTH, HEIGHT, PAD = 900, 1190, 15


def _scalers(page: Page, width: int, height: int, pad: int):
    def sx(x: int) -> float:
        return pad + (x / page.max_x) * (width - 2 * pad)

    def sy(y: int) -> float:
        return pad + (y / page.max_y) * (height - 2 * pad)
    return sx, sy


def render_svg(page: Page, width: int = WIDTH, height: int = HEIGHT, pad: int = PAD) -> str:
    """strokes -> SVG paths. Origin top-left, no axis flip (non-A4 device)."""
    sx, sy = _scalers(page, width, height, pad)
    paths = []
    for s in page.strokes:
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{sx(p.x):.1f},{sy(p.y):.1f}"
            for i, p in enumerate(s)
        )
        paths.append(f'<path d="{d}" fill="none" stroke="#111" stroke-width="2.5"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:#fff">' + "".join(paths) + "</svg>"
    )


def render_json(page: Page) -> str:
    """Ordered, lossless point dump (same schema as the CLI)."""
    return json.dumps(
        {
            "page": page.index,
            "max_x": page.max_x,
            "max_y": page.max_y,
            "max_press": page.max_press,
            "complete": page.complete,
            "strokes": [
                [{"x": p.x, "y": p.y, "press": p.press, "pen_down": p.pen_down} for p in s]
                for s in page.strokes
            ],
        }
    )


def render_png(page: Page, width: int = WIDTH, height: int = HEIGHT, pad: int = PAD) -> bytes:
    """Rasterise with pressure-scaled line width."""
    from PIL import Image, ImageDraw  # lazy: keeps the rest of the module stdlib-only

    # Draw at 2x and downsample for cheap anti-aliasing.
    k = 2
    img = Image.new("RGB", (width * k, height * k), "white")
    draw = ImageDraw.Draw(img)
    sx, sy = _scalers(page, width * k, height * k, pad * k)
    for s in page.strokes:
        for prev, p in zip(s, s[1:]):
            w = max(1, round((1.5 + 2.5 * (p.press / page.max_press)) * k))
            a, b = (sx(prev.x), sy(prev.y)), (sx(p.x), sy(p.y))
            draw.line([a, b], fill=(17, 17, 17), width=w)
            r = w / 2  # round caps/joins
            draw.ellipse([b[0] - r, b[1] - r, b[0] + r, b[1] + r], fill=(17, 17, 17))
    img = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
