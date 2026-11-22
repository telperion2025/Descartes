"""Database and data commands: /DATA FILL, SORT, QUERY, TABLE, FREQUENCY,
REGRESSION. Pure logic over the Worksheet + Engine (no curses).

String sort order follows 1-2-3 exactly: numbers before text, and text as
digits (0-9) < uppercase A-Z < lowercase a-z (distinct from general text
comparison, which is case-insensitive).
"""

from __future__ import annotations

import math

from .engine import Engine, FormulaSyntaxError, parse, shift, serialize
from .functions import Error, lotus_compare
from .grid import (FORMULA, LABEL, NUMBER, Worksheet, normalize_corners,
                   range_cells)


# ---------------------------------------------------------------------------
# 1-2-3 sort order
# ---------------------------------------------------------------------------

def _char_rank(ch: str) -> int:
    if ch.isdigit():
        return 0
    if ch.isupper():
        return 1
    if ch.islower():
        return 2
    return 3


def lotus_sort_key(v):
    """Sort key implementing the 1-2-3 collation: numbers < text;
    text ordered digits < A-Z < a-z, then other chars by code."""
    if v is None:
        return (0, 0.0, "")
    if isinstance(v, Error):
        return (2, 0.0, v.code)
    if isinstance(v, bool):
        return (1, float(v), "")
    if isinstance(v, (int, float)):
        return (1, float(v), "")
    s = str(v)
    return (2, 0.0, "".join(chr(ord(ch) + _char_rank(ch) * 256) for ch in s))


# ---------------------------------------------------------------------------
# /DATA FILL
# ---------------------------------------------------------------------------

def fill(sheet: Worksheet, engine: Engine, rng, start: float, step: float, stop: float | None = None):
    """Fill a range with start, start+step, ... (optionally <= stop)."""
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    horizontal = (r1 == r2 and c2 > c1)
    vertical = (c1 == c2 and r2 > r1)
    if not horizontal and not vertical:
        # fill row-major
        horizontal = (c2 - c1) >= (r2 - r1)
    keys = list(range_cells((c1, r1), (c2, r2)))
    if not horizontal:
        keys.sort(key=lambda k: (k[0], k[1]))
    v = start
    for (c, r) in keys:
        if stop is not None and ((step >= 0 and v > stop) or (step < 0 and v < stop)):
            break
        sheet.set_entry(c, r, repr(v) if isinstance(v, float) else str(v))
        engine.note_edit((c, r))
        v += step
    engine.recalc()


# ---------------------------------------------------------------------------
# /DATA SORT
# ---------------------------------------------------------------------------

def sort_range(sheet: Worksheet, engine: Engine, rng, keys):
    """Stable sort of rng rows by keys = [(col_offset, ascending), ...].

    keys are 0-based offsets from the range's first column.
    """
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    rows = []
    for r in range(r1, r2 + 1):
        rows.append([sheet.cells.get((c, r)) for c in range(c1, c2 + 1)])

    def keyfn(row):
        parts = []
        for off, asc in keys:
            cell = row[off] if 0 <= off < len(row) else None
            v = cell.value if cell is not None else None
            k = lotus_sort_key(v)
            parts.append(k if asc else _invert(k))
        return tuple(parts)

    order = sorted(range(len(rows)), key=lambda i: keyfn(rows[i]))
    for new_r, old_i in enumerate(order):
        r = r1 + new_r
        for c_off, old_cell in enumerate(rows[old_i]):
            c = c1 + c_off
            if old_cell is None:
                sheet.clear_cell(c, r)
            else:
                sheet.cells[(c, r)] = old_cell
    engine.register_all()
    engine.recalc(force=True)


class _Invert:
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k

    def __lt__(self, other):
        return other.k < self.k

    def __eq__(self, other):
        return other.k == self.k


def _invert(k):
    return _Invert(k)


# ---------------------------------------------------------------------------
# /DATA QUERY
# ---------------------------------------------------------------------------

def _cell_matches(criterion, value, engine, data_row, col, first_data_row) -> bool:
    """Evaluate one criterion cell against one data row value.

    Criterion formulas are written against the input range's first data row;
    they are shifted down to the row being tested.
    """
    if criterion is None:
        return True
    raw = criterion.raw
    if raw is None:
        return True
    if criterion.kind == FORMULA:
        try:
            from .engine import Ctx, compile_node
            ast = shift(parse(raw), 0, data_row - first_data_row)
            fn = compile_node(ast)
            ctx = Ctx(engine, (col, data_row))
            v = fn(ctx)
            if isinstance(v, Error):
                return False
            if isinstance(v, str):
                return v != ""
            return v not in (0, 0.0, None)
        except FormulaSyntaxError:
            return False
    # text/number criterion with optional operator prefix and wildcards
    text = str(criterion.value) if criterion.value is not None else ""
    for op in ("<=", ">=", "<>", "=", "<", ">"):
        if text.startswith(op):
            rest = text[len(op):]
            try:
                bound = float(rest)
                num = value if isinstance(value, (int, float)) else None
                if num is None:
                    return False
                return {"<": num < bound, ">": num > bound, "<=": num <= bound,
                        ">=": num >= bound, "=": num == bound, "<>": num != bound}[op]
            except ValueError:
                return _wildcard_match(rest, value)
    if isinstance(value, str):
        return _wildcard_match(text, value)
    if isinstance(value, (int, float)):
        try:
            return float(text) == float(value)
        except ValueError:
            return False
    return text == ""


def _wildcard_match(pattern: str, value) -> bool:
    if not isinstance(value, str):
        return False
    import re
    rx = ""
    for ch in pattern:
        if ch == "*":
            rx += ".*"
        elif ch == "?":
            rx += "."
        else:
            rx += re.escape(ch)
    return re.fullmatch(rx, value, re.IGNORECASE | re.DOTALL) is not None


def query(sheet: Worksheet, engine: Engine, input_rng, crit_rng,
          output_rng=None, unique=False, action="find"):
    """/DATA QUERY. action: 'find' | 'delete' | 'extract'.

    Returns the list of matching data rows (0-based absolute rows).
    """
    (ic1, ir1), (ic2, ir2) = normalize_corners(*input_rng)
    (cc1, cr1), (cc2, cr2) = normalize_corners(*crit_rng)
    headers = [sheet.value_at(c, ir1) for c in range(ic1, ic2 + 1)]
    # map criterion columns to input columns by header text
    crit_cols = []
    for c in range(cc1, cc2 + 1):
        h = sheet.value_at(c, cr1)
        match = None
        for i, ih in enumerate(headers):
            if isinstance(ih, str) and isinstance(h, str) and ih.upper() == h.upper():
                match = i
                break
        crit_cols.append((c, match))
    crit_rows = list(range(cr1 + 1, cr2 + 1))
    if not crit_rows:
        crit_rows = [cr1 + 1]  # empty criterion = match all
    matches = []
    for r in range(ir1 + 1, ir2 + 1):
        ok = False
        for cr in crit_rows:
            row_ok = True
            for (c, col_off) in crit_cols:
                if col_off is None:
                    continue
                crit = sheet.cells.get((c, cr))
                value = sheet.value_at(ic1 + col_off, r)
                if not _cell_matches(crit, value, engine, r, ic1 + col_off, ir1 + 1):
                    row_ok = False
                    break
            if row_ok:
                ok = True
                break
        if ok:
            matches.append(r)
    if action == "delete":
        for r in reversed(matches):
            for c in range(ic1, ic2 + 1):
                sheet.clear_cell(c, r)
        engine.register_all()
        engine.recalc(force=True)
    elif action == "extract" and output_rng is not None:
        (oc1, or1), _ = normalize_corners(*output_rng)
        seen = set()
        out_r = or1
        # header row
        for i, h in enumerate(headers):
            sheet.set_entry(oc1 + i, out_r, str(h) if h is not None else "")
        engine.note_edit((oc1, out_r))
        out_r += 1
        for r in matches:
            if unique:
                key = tuple(_hashable(sheet.value_at(ic1 + i, r)) for i in range(ic2 - ic1 + 1))
                if key in seen:
                    continue
                seen.add(key)
            for i in range(ic2 - ic1 + 1):
                src = sheet.cells.get((ic1 + i, r))
                if src is None:
                    sheet.clear_cell(oc1 + i, out_r)
                else:
                    cell = src.copy()
                    sheet.cells[(oc1 + i, out_r)] = cell
            out_r += 1
        engine.register_all()
        engine.recalc(force=True)
    return matches


def _hashable(v):
    if isinstance(v, Error):
        return ("ERR", v.code)
    return v


# ---------------------------------------------------------------------------
# /DATA TABLE (what-if, 1-var and 2-var)
# ---------------------------------------------------------------------------

def data_table_1var(sheet: Worksheet, engine: Engine, input_cell, formula_cell,
                    rng, values):
    """1-variable table: rows of `values` substituted into input_cell,
    formula_cell evaluated; results written beside the value column."""
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    ic, ir = input_cell
    fc, fr = formula_cell
    # rng: first column = input values (below the top-left formula cell),
    # results are written in the column to the right
    for r in range(r1 + 1, r2 + 1):
        v = values[r - r1 - 1] if r - r1 - 1 < len(values) else sheet.value_at(c1, r)
        sheet.set_entry(ic, ir, repr(v))
        engine.note_edit((ic, ir))
        engine.recalc(force=True)
        result = sheet.value_at(fc, fr)
        sheet.set_entry(c1 + 1, r, _num_to_raw(result))
        engine.note_edit((c1 + 1, r))
    engine.recalc(force=True)


def data_table_2var(sheet, engine, input_cell_row, input_cell_col, formula_cell, rng):
    (c1, r1), (c2, r2) = normalize_corners(*rng)
    (fc, fr) = formula_cell
    col_vals = [sheet.value_at(c, r1) for c in range(c1 + 1, c2 + 1)]
    for ci, cv in enumerate(col_vals):
        sheet.set_entry(input_cell_col, input_cell_col[1], repr(cv) if cv is not None else "0")
        engine.note_edit(input_cell_col)
        for ri in range(r1 + 1, r2 + 1):
            rv = sheet.value_at(c1, ri)
            sheet.set_entry(input_cell_row[0], input_cell_row[1],
                            repr(rv) if rv is not None else "0")
            engine.note_edit(input_cell_row)
            engine.recalc(force=True)
            result = sheet.value_at(fc, fr)
            sheet.set_entry(c1 + 1 + ci, ri, _num_to_raw(result))
            engine.note_edit((c1 + 1 + ci, ri))
    engine.recalc(force=True)


def _num_to_raw(v):
    if v is None:
        return ""
    if isinstance(v, Error):
        return "@ERR"
    if isinstance(v, str):
        return v
    from . import fmt as _fmt
    return _fmt.general_number(float(v))


# ---------------------------------------------------------------------------
# /DATA FREQUENCY
# ---------------------------------------------------------------------------

def frequency(sheet: Worksheet, engine: Engine, data_rng, bin_rng):
    """Write counts next to (right of) the bin range. Returns counts."""
    (dc1, dr1), (dc2, dr2) = normalize_corners(*data_rng)
    (bc1, br1), (bc2, br2) = normalize_corners(*bin_rng)
    values = []
    for c, r in range_cells((dc1, dr1), (dc2, dr2)):
        v = sheet.value_at(c, r)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values.append(float(v))
    bins = []
    for c, r in range_cells((bc1, br1), (bc2, br2)):
        v = sheet.value_at(c, r)
        bins.append(float(v) if isinstance(v, (int, float)) else math.inf)
    counts = [0] * (len(bins) + 1)
    for v in values:
        placed = False
        for i, b in enumerate(bins):
            if v <= b:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    out_c = bc2 + 1
    for i, cnt in enumerate(counts):
        r = br1 + i if i < len(bins) else br2 + 1
        sheet.set_entry(out_c, r, str(cnt))
        engine.note_edit((out_c, r))
    engine.recalc()
    return counts


# ---------------------------------------------------------------------------
# /DATA REGRESSION (simple linear regression y = a + b x)
# ---------------------------------------------------------------------------

def regression(sheet: Worksheet, engine: Engine, x_rng, y_rng, out_cell):
    """Write a stats block starting at out_cell: slope, intercept, r^2,
    std err of slope, std err of intercept, n, and the x/y means."""
    (xc1, xr1), (xc2, xr2) = normalize_corners(*x_rng)
    (yc1, yr1), (yc2, yr2) = normalize_corners(*y_rng)
    xs = [sheet.value_at(c, r) for c, r in range_cells((xc1, xr1), (xc2, xr2))]
    ys = [sheet.value_at(c, r) for c, r in range_cells((yc1, yr1), (yc2, yr2))]
    pts = [(float(x), float(y)) for x, y in zip(xs, ys)
           if isinstance(x, (int, float)) and isinstance(y, (int, float))
           and not isinstance(x, bool) and not isinstance(y, bool)]
    n = len(pts)
    if n < 2:
        raise ValueError("regression needs at least 2 numeric points")
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx == 0:
        raise ValueError("x values have zero variance")
    b = sxy / sxx
    a = my - b * mx
    r2 = (sxy * sxy / (sxx * syy)) if syy > 0 else 1.0
    # standard errors
    if n > 2:
        resid = sum((p[1] - (a + b * p[0])) ** 2 for p in pts)
        s2 = resid / (n - 2)
        se_b = math.sqrt(s2 / sxx)
        se_a = math.sqrt(s2 * (1 / n + mx * mx / sxx))
    else:
        se_b = se_a = 0.0
    oc, orr = out_cell
    rows = [
        ("Slope", b), ("Intercept", a), ("R^2", r2),
        ("SE slope", se_b), ("SE intercept", se_a),
        ("n", float(n)), ("Mean X", mx), ("Mean Y", my),
    ]
    for i, (label, val) in enumerate(rows):
        sheet.set_entry(oc, orr + i, label)
        sheet.set_entry(oc + 1, orr + i, _num_to_raw(val))
        engine.note_edit((oc, orr + i))
        engine.note_edit((oc + 1, orr + i))
    engine.recalc()
    return {"slope": b, "intercept": a, "r2": r2, "n": n}
