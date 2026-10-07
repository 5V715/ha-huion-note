"""Render decoded pages to SVG, PDF (vectors), JSON (ordered lossless points —
input for handwriting recognition) and PNG (Pillow).

Line width is given in millimetres at full pen pressure and tapers with pressure
(down to MIN_PRESSURE_FACTOR of it), like a ballpoint. Millimetres assume Huion's
usual 5080 lines per inch, which puts the X10's 28200 x 37400 area at about
141 x 187 mm; that only sets the physical scale (PDF page size, mm line width).
"""
from __future__ import annotations

import io
import json
import zlib

from .codec import Page, StylusPoint

UNITS_PER_MM = 5080 / 25.4      # device units per millimetre (assumed 5080 LPI)
PT_PER_MM = 72 / 25.4
DEFAULT_LINE_WIDTH_MM = 0.3     # at full pressure
MIN_PRESSURE_FACTOR = 0.4       # the lightest touch draws 40 % of the full width
INK = (17, 17, 17)

SVG_WIDTH = 900                 # px
PNG_WIDTH = 1240                # px, ~220 dpi at the notebook's real size
PAD_FRACTION = 15 / 900         # white margin around PNG/SVG, as a share of the width


def line_width_mm(page: Page, p: StylusPoint, full_width_mm: float) -> float:
    pressure = min(max(p.press / page.max_press, 0.0), 1.0) if page.max_press else 1.0
    return full_width_mm * (MIN_PRESSURE_FACTOR + (1 - MIN_PRESSURE_FACTOR) * pressure)


class _Canvas:
    """Maps device units to a raster/SVG canvas `width` px wide, keeping the aspect."""

    def __init__(self, page: Page, width: float):
        self.pad = width * PAD_FRACTION
        self.scale = (width - 2 * self.pad) / page.max_x  # px per device unit
        self.width = width
        self.height = page.max_y * self.scale + 2 * self.pad
        self.px_per_mm = self.scale * UNITS_PER_MM

    def xy(self, p: StylusPoint) -> tuple[float, float]:
        return self.pad + p.x * self.scale, self.pad + p.y * self.scale


def render_svg(page: Page, width: int = SVG_WIDTH,
               full_width_mm: float = DEFAULT_LINE_WIDTH_MM) -> str:
    """strokes -> SVG paths, one per stroke at the stroke's mean pressure.
    Origin top-left, no axis flip (non-A4 device)."""
    c = _Canvas(page, width)
    paths = []
    for s in page.strokes:
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
            for i, (x, y) in enumerate(c.xy(p) for p in s)
        )
        w = sum(line_width_mm(page, p, full_width_mm) for p in s) / len(s) * c.px_per_mm
        paths.append(
            f'<path d="{d}" fill="none" stroke="#111" stroke-width="{w:.2f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{c.width:.0f}" '
        f'height="{c.height:.0f}" style="background:#fff">' + "".join(paths) + "</svg>"
    )


def render_pdf(page: Page, full_width_mm: float = DEFAULT_LINE_WIDTH_MM) -> bytes:
    """Vector PDF at the notebook's real size, straight from the stroke data.
    Width follows pressure along each stroke (runs of equal width share a path)."""
    pt = PT_PER_MM / UNITS_PER_MM          # points per device unit
    width, height = page.max_x * pt, page.max_y * pt
    ink = " ".join(f"{v / 255:.3f}" for v in INK)
    ops = [f"1 J 1 j {ink} RG"]            # round caps/joins, ink colour

    def xy(p: StylusPoint) -> str:
        return f"{p.x * pt:.2f} {height - p.y * pt:.2f}"  # PDF origin is bottom-left

    for s in page.strokes:
        current = None
        for prev, p in zip(s, s[1:]):
            # quantise to 0.05 pt so a stroke becomes a few paths, not one per segment
            w = round(line_width_mm(page, p, full_width_mm) * PT_PER_MM * 20) / 20
            if w != current:
                if current is not None:
                    ops.append("S")
                ops.append(f"{w:.2f} w {xy(prev)} m")
                current = w
            ops.append(f"{xy(p)} l")
        if current is not None:
            ops.append("S")
    return _pdf_document(width, height, zlib.compress("\n".join(ops).encode()))


def _pdf_document(width: float, height: float, content: bytes) -> bytes:
    """A minimal single-page PDF 1.4 with one Flate-compressed content stream."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
         f"/Resources << >> /Contents 4 0 R >>").encode(),
        f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode()
        + content + b"\nendstream",
        b"<< /Producer (Huion Note X10 for Home Assistant) >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def render_json(page: Page) -> str:
    """Ordered, lossless point dump (same schema as the CLI)."""
    return json.dumps(
        {
            "page": page.index,
            "max_x": page.max_x,
            "max_y": page.max_y,
            "max_press": page.max_press,
            "complete": page.complete,
            # Lossless: every point in stream order, incl. pen-up points and dots.
            "points": [
                {"x": p.x, "y": p.y, "press": p.press, "pen_down": p.pen_down}
                for p in page.points
            ],
            "strokes": [
                [{"x": p.x, "y": p.y, "press": p.press, "pen_down": p.pen_down} for p in s]
                for s in page.strokes
            ],
        }
    )


def render_png(page: Page, width: int = PNG_WIDTH,
               full_width_mm: float = DEFAULT_LINE_WIDTH_MM) -> bytes:
    """Rasterise with pressure-scaled line width."""
    from PIL import Image, ImageDraw  # lazy: keeps the rest of the module stdlib-only

    # Draw at 3x and downsample, so thin lines come out smooth instead of jagged.
    k = 3
    c = _Canvas(page, width * k)
    img = Image.new("RGB", (round(c.width), round(c.height)), "white")
    draw = ImageDraw.Draw(img)

    def dot(xy: tuple[float, float], w: float) -> None:  # round caps/joins
        r = w / 2
        draw.ellipse([xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r], fill=INK)

    for s in page.strokes:
        dot(c.xy(s[0]), max(1.0, line_width_mm(page, s[0], full_width_mm) * c.px_per_mm))
        for prev, p in zip(s, s[1:]):
            w = max(1.0, line_width_mm(page, p, full_width_mm) * c.px_per_mm)
            a, b = c.xy(prev), c.xy(p)
            draw.line([a, b], fill=INK, width=max(1, round(w)))
            dot(b, w)
    img = img.resize((round(c.width / k), round(c.height / k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
