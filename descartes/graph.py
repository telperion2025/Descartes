"""Text-mode graph renderer (pure logic; no curses).

Renders one of line / bar / xy / stacked-bar / pie into a viewport of
``width`` x ``height`` characters, returned as a list of strings. Bars use
'#' (or a half-block when UTF-8 is allowed), axes use - | +, series glyphs
are the Unicode set with an ASCII fallback.
"""

from __future__ import annotations

from .grid import format_range, normalize_corners, range_cells

GRAPH_TYPES = ("LINE", "BAR", "XY", "STACKED-BAR", "PIE")

SERIES_GLYPHS_UTF8 = "□■▲▼○*"
SERIES_GLYPHS_ASCII = "o#*+x."
BAR_GLYPHS_UTF8 = "█▓▒░"
BAR_GLYPH_ASCII = "#"


class GraphDef:
    """A saved graph definition (persists in .vg1)."""

    def __init__(self, gtype="LINE", x_range=None, series=None, title="",
                 legend=None, options=None):
        self.type = gtype.upper()
        self.x_range = x_range          # ((c1,r1),(c2,r2)) or None
        self.series = series or []      # list of {"name":..., "range": ((..),(..))}
        self.title = title
        self.options = options or {}

    def to_dict(self):
        return {
            "type": self.type,
            "x_range": [list(self.x_range[0]), list(self.x_range[1])] if self.x_range else None,
            "series": [{"name": s.get("name", ""), "range": [list(s["range"][0]), list(s["range"][1])]}
                       for s in self.series],
            "title": self.title,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, d):
        xr = None
        if d.get("x_range"):
            (c1, r1), (c2, r2) = d["x_range"]
            xr = ((c1, r1), (c2, r2))
        series = []
        for s in d.get("series", []):
            (c1, r1), (c2, r2) = s["range"]
            series.append({"name": s.get("name", ""), "range": ((c1, r1), (c2, r2))})
        return cls(d.get("type", "LINE"), xr, series, d.get("title", ""), options=d.get("options", {}))


def _series_values(sheet, rng):
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    vals = []
    for c, r in range_cells((c1, r1), (c2, r2)):
        v = sheet.value_at(c, r)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            vals.append(None)
        else:
            vals.append(float(v))
    return vals


def _x_labels(sheet, rng):
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    out = []
    for c, r in range_cells((c1, r1), (c2, r2)):
        v = sheet.value_at(c, r)
        out.append("" if v is None else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v)))
    return out


def _nice_ticks(lo, hi, count):
    """3-6 'nice' gridline values between lo and hi."""
    if lo == hi:
        lo -= 1
        hi += 1
    span = hi - lo
    raw = span / max(1, count - 1)
    mag = 10 ** int(__import__("math").floor(__import__("math").log10(raw))) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if span / step <= count:
            break
    import math
    start = math.ceil(lo / step) * step
    ticks = []
    t = start
    while t <= hi + 1e-12:
        ticks.append(t)
        t += step
    return ticks


def render(graph: GraphDef, sheet, width: int, height: int, utf8: bool = True) -> list[str]:
    """Render the graph into a list of `height` strings of `width` chars."""
    width = max(20, width)
    height = max(6, height)
    if graph.type == "PIE":
        return _render_pie(graph, sheet, width, height, utf8)
    return _render_cartesian(graph, sheet, width, height, utf8)


def _render_cartesian(graph, sheet, width, height, utf8):
    series = [(s.get("name", f"Series {i+1}"), _series_values(sheet, s["range"]))
              for i, s in enumerate(graph.series) if s.get("range")]
    n = max((len(v) for _, v in series), default=0)
    if graph.x_range:
        labels = _x_labels(sheet, graph.x_range)
        n = max(n, len(labels))
    else:
        labels = [str(i + 1) for i in range(n)]
    n = max(n, 1)

    all_vals = [v for _, vals in series for v in vals if v is not None]
    if graph.type in ("BAR", "STACKED-BAR"):
        lo = min(0.0, min(all_vals)) if all_vals else 0.0
        hi = max(0.0, max(all_vals)) if all_vals else 1.0
    else:
        lo = min(all_vals) if all_vals else 0.0
        hi = max(all_vals) if all_vals else 1.0
    if hi == lo:
        hi = lo + 1.0

    plot_h = height - 4          # room for axis labels + legend
    plot_w = width - 10           # room for y-axis labels
    if plot_h < 2 or plot_w < 4:
        plot_h, plot_w = max(2, plot_h), max(4, plot_w)

    grid = [[" "] * plot_w for _ in range(plot_h)]
    ticks = _nice_ticks(lo, hi, 6)

    def y_of(v):
        return int(round((hi - v) / (hi - lo) * (plot_h - 1)))

    # gridlines (horizontal + vertical), toggleable via /GRAPH OPTIONS GRID
    if graph.options.get("grid", True):
        for t in ticks:
            y = y_of(t)
            if 0 <= y < plot_h:
                for x in range(plot_w):
                    if grid[y][x] == " ":
                        grid[y][x] = "-"
        if n > 1:
            for i in range(n):
                x = int(i * (plot_w - 1) / (n - 1))
                for y in range(plot_h):
                    if grid[y][x] == " ":
                        grid[y][x] = "|"

    if graph.type == "STACKED-BAR":
        _draw_stacked_bars(grid, series, n, plot_w, plot_h, lo, hi, utf8)
    elif graph.type == "BAR":
        _draw_bars(grid, series, n, plot_w, plot_h, lo, hi, utf8)
    else:  # LINE and XY
        glyphs = SERIES_GLYPHS_UTF8 if utf8 else SERIES_GLYPHS_ASCII
        for si, (_, vals) in enumerate(series):
            glyph = glyphs[si % len(glyphs)]
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                x = int(round(i / max(1, n - 1) * (plot_w - 1))) if n > 1 else plot_w // 2
                pts.append((x, y_of(v), glyph))
            for x, y, g in pts:
                if 0 <= y < plot_h and 0 <= x < plot_w:
                    grid[y][x] = g
            # connect with '.' when utf8 off, else leave glyphs
            if not utf8:
                for (x0, y0, _), (x1, y1, _) in zip(pts, pts[1:]):
                    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
                    for k in range(1, steps):
                        x = x0 + (x1 - x0) * k // steps
                        y = y0 + (y1 - y0) * k // steps
                        if 0 <= y < plot_h and 0 <= x < plot_w and grid[y][x] == " ":
                            grid[y][x] = "."

    # compose output: y-axis labels + plot + x-axis + labels + legend
    lines = []
    if graph.title:
        lines.append(graph.title[:width].center(width))
    for y in range(plot_h):
        row = grid[y]
        label = ""
        for t in ticks:
            if y_of(t) == y:
                label = _fmt_tick(t)
                break
        lines.append(f"{label:>8} |" + "".join(row))
    lines.append(" " * 8 + " +" + "-" * plot_w)
    # x labels (compressed)
    xline = [" "] * plot_w
    if labels:
        step = max(1, (len(labels) + plot_w - 1) // plot_w)
        for i in range(0, len(labels), step):
            x = int(round(i / max(1, n - 1) * (plot_w - 1))) if n > 1 else plot_w // 2
            lab = labels[i]
            for k, ch in enumerate(lab[:6]):
                if 0 <= x + k < plot_w:
                    xline[x + k] = ch
    lines.append(" " * 9 + "".join(xline))
    glyphs = SERIES_GLYPHS_UTF8 if utf8 else SERIES_GLYPHS_ASCII
    legend = "  ".join(f"{glyphs[i % len(glyphs)]} {name}" for i, (name, _) in enumerate(series))
    lines.append(legend[:width])
    while len(lines) < height:
        lines.append("")
    return [ln[:width].ljust(width) for ln in lines[:height]]


def _fmt_tick(v):
    if v == int(v) and abs(v) < 1e12:
        return str(int(v))
    return f"{v:.3g}"


def _draw_bars(grid, series, n, plot_w, plot_h, lo, hi, utf8):
    nseries = len(series)
    bar_w = max(1, plot_w // max(1, n))
    fill = BAR_GLYPHS_UTF8[0] if utf8 else BAR_GLYPH_ASCII

    def y_of(v):
        return int(round((hi - v) / (hi - lo) * (plot_h - 1)))

    zero_y = y_of(0.0)
    for i in range(n):
        x = int(round(i / max(1, n - 1) * (plot_w - 1))) if n > 1 else plot_w // 2
        for si, (_, vals) in enumerate(series):
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            y = y_of(v)
            lo_y, hi_y = min(y, zero_y), max(y, zero_y)
            for yy in range(lo_y, hi_y + 1):
                if 0 <= yy < plot_h and 0 <= x < plot_w:
                    grid[yy][x] = fill


def _draw_stacked_bars(grid, series, n, plot_w, plot_h, lo, hi, utf8):
    fills = BAR_GLYPHS_UTF8 if utf8 else [BAR_GLYPH_ASCII] * 6

    def y_of(v):
        return int(round((hi - v) / (hi - lo) * (plot_h - 1)))

    for i in range(n):
        x = int(round(i / max(1, n - 1) * (plot_w - 1))) if n > 1 else plot_w // 2
        pos = 0.0
        neg = 0.0
        for si, (_, vals) in enumerate(series):
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            base = pos if v >= 0 else neg
            top = base + v
            y0 = y_of(max(base, top))
            y1 = y_of(min(base, top))
            fill = fills[si % len(fills)]
            for yy in range(y0, y1 + 1):
                if 0 <= yy < plot_h and 0 <= x < plot_w:
                    grid[yy][x] = fill
            if v >= 0:
                pos = top
            else:
                neg = top


def _render_pie(graph, sheet, width, height, utf8):
    """Pie: horizontal proportion bar with percentage labels (documented
    text-mode compromise for a true pie)."""
    series = [(s.get("name", f"Slice {i+1}"), _series_values(sheet, s["range"]))
              for i, s in enumerate(graph.series) if s.get("range")]
    slices = []
    for name, vals in series:
        total = sum(v for v in vals if v is not None and v > 0)
        slices.append((name, total if total > 0 else 0.0))
    grand = sum(v for _, v in slices)
    lines = []
    if graph.title:
        lines.append(graph.title[:width].center(width))
    bar_w = width - 2
    fills = BAR_GLYPHS_UTF8 if utf8 else [BAR_GLYPH_ASCII] * 6
    if grand <= 0:
        lines.append("(no positive data)")
        while len(lines) < height:
            lines.append("")
        return [ln[:width].ljust(width) for ln in lines[:height]]
    bar = []
    for i, (name, v) in enumerate(slices):
        count = int(round(v / grand * bar_w))
        bar.extend([fills[i % len(fills)]] * count)
    bar = (bar + [" "] * bar_w)[:bar_w]
    lines.append("+" + "".join(bar) + "+")
    for i, (name, v) in enumerate(slices):
        pct = v / grand * 100.0
        lines.append(f"{fills[i % len(fills)]} {name}: {pct:.1f}%")
    while len(lines) < height:
        lines.append("")
    return [ln[:width].ljust(width) for ln in lines[:height]]
