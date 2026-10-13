"""Render decoded pages to SVG, PDF (vectors), JSON (ordered lossless points —
input for handwriting recognition) and PNG (Pillow).

Line width is given in millimetres at full pen pressure and tapers with pressure
(down to MIN_PRESSURE_FACTOR of it), like a ballpoint. Pressure is read relative to
the page: each page's own light-to-heavy range (5th-95th percentile of pen-down
points) is stretched over thin-to-full, so a page written with uniformly light
pressure still shows its variation. The taper is strong on purpose: at whole-page
zoom anything under about a screen pixel looks the same, so a mild ratio (the old
40 %) reads as uniform lines. Millimetres assume Huion's
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
MIN_PRESSURE_FACTOR = 0.15      # the lightest touch draws 15 % of the full width
DEFAULT_PRESSURE_EFFECT = 1.0   # 0 = uniform lines, 1 = full taper
MIN_PRESSURE_SPREAD = 0.03      # a page spanning less of the range counts as uniform
SMOOTHING = 2                   # points either side averaged into each width
INK = (17, 17, 17)

SVG_WIDTH = 900                 # px
PNG_WIDTH = 1240                # px, ~220 dpi at the notebook's real size
PAD_FRACTION = 15 / 900         # white margin around PNG/SVG, as a share of the width


def pressure_range(page: Page) -> tuple[float, float]:
    """The page's own light and heavy pressure (5th/95th percentile of pen-down
    points). Falls back to the pen's full range when the page is nearly uniform,
    so sensor noise isn't blown up into visible width changes."""
    full = (0.0, page.max_press or 1.0)
    vals = sorted(p.press for s in page.strokes for p in s)
    if not vals:
        return full
    lo, hi = vals[len(vals) * 5 // 100], vals[min(len(vals) - 1, len(vals) * 95 // 100)]
    if hi - lo < MIN_PRESSURE_SPREAD * full[1]:
        return full
    return float(lo), float(hi)


def line_width_mm(page: Page, p: StylusPoint, full_width_mm: float,
                  rng: tuple[float, float] | None = None,
                  pressure_effect: float = DEFAULT_PRESSURE_EFFECT) -> float:
    """Width of the line at point `p`. Without `rng` pressure is read on the pen's
    absolute scale (0..max_press); with it, relative to that range."""
    lo, hi = rng or (0.0, page.max_press or 1.0)
    t = min(max((p.press - lo) / (hi - lo), 0.0), 1.0) if hi > lo else 1.0
    thinnest = 1 - (1 - MIN_PRESSURE_FACTOR) * min(max(pressure_effect, 0.0), 1.0)
    return full_width_mm * (thinnest + (1 - thinnest) * t)


def stroke_widths_mm(page: Page, stroke: list[StylusPoint], full_width_mm: float,
                     rng: tuple[float, float],
                     pressure_effect: float = DEFAULT_PRESSURE_EFFECT) -> list[float]:
    """Per-point widths along a stroke, lightly smoothed (moving average)."""
    raw = [line_width_mm(page, p, full_width_mm, rng, pressure_effect) for p in stroke]
    out = []
    for i in range(len(raw)):
        win = raw[max(0, i - SMOOTHING): i + SMOOTHING + 1]
        out.append(sum(win) / len(win))
    return out


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
               full_width_mm: float = DEFAULT_LINE_WIDTH_MM,
               pressure_effect: float = DEFAULT_PRESSURE_EFFECT) -> str:
    """strokes -> SVG paths, one per stroke at the stroke's mean width.
    Origin top-left, no axis flip (non-A4 device)."""
    c = _Canvas(page, width)
    rng = pressure_range(page)
    paths = []
    for s in page.strokes:
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
            for i, (x, y) in enumerate(c.xy(p) for p in s)
        )
        widths = stroke_widths_mm(page, s, full_width_mm, rng, pressure_effect)
        w = sum(widths) / len(widths) * c.px_per_mm
        paths.append(
            f'<path d="{d}" fill="none" stroke="#111" stroke-width="{w:.2f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{c.width:.0f}" '
        f'height="{c.height:.0f}" style="background:#fff">' + "".join(paths) + "</svg>"
    )


def render_pdf(page: Page, full_width_mm: float = DEFAULT_LINE_WIDTH_MM,
               pressure_effect: float = DEFAULT_PRESSURE_EFFECT) -> bytes:
    """Vector PDF at the notebook's real size, straight from the stroke data.
    Width follows pressure along each stroke (runs of equal width share a path)."""
    pt = PT_PER_MM / UNITS_PER_MM          # points per device unit
    width, height = page.max_x * pt, page.max_y * pt
    ink = " ".join(f"{v / 255:.3f}" for v in INK)
    ops = [f"1 J 1 j {ink} RG"]            # round caps/joins, ink colour
    rng = pressure_range(page)

    def xy(p: StylusPoint) -> str:
        return f"{p.x * pt:.2f} {height - p.y * pt:.2f}"  # PDF origin is bottom-left

    for s in page.strokes:
        current = None
        widths = stroke_widths_mm(page, s, full_width_mm, rng, pressure_effect)
        for (prev, p), w_mm in zip(zip(s, s[1:]), widths[1:]):
            # quantise to 0.02 pt so a stroke becomes runs of paths, not one per segment
            w = round(w_mm * PT_PER_MM * 50) / 50
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
               full_width_mm: float = DEFAULT_LINE_WIDTH_MM,
               pressure_effect: float = DEFAULT_PRESSURE_EFFECT) -> bytes:
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

    rng = pressure_range(page)
    for s in page.strokes:
        widths = [max(1.0, w * c.px_per_mm)
                  for w in stroke_widths_mm(page, s, full_width_mm, rng, pressure_effect)]
        dot(c.xy(s[0]), widths[0])
        for (prev, p), w in zip(zip(s, s[1:]), widths[1:]):
            a, b = c.xy(prev), c.xy(p)
            draw.line([a, b], fill=INK, width=max(1, round(w)))
            dot(b, w)
    img = img.resize((round(c.width / k), round(c.height / k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
