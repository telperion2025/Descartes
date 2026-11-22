"""Worksheet grid model: cells, columns, ranges and addressing.

Grid limits follow Lotus 1-2-3 Release 3.1: 256 columns (A..IV) by 8192 rows.
Cells are keyed as ``(sheet_name, col, row)`` with zero-based col/row integers so
that a later phase can add multiple worksheets and 3-D ranges without migration;
this module stores the per-sheet part of that key.
"""

from __future__ import annotations

MAX_COLS = 256          # A..IV
MAX_ROWS = 8192         # 1..8192
DEFAULT_COL_WIDTH = 9
MAX_COL_WIDTH = 240

EMPTY = "EMPTY"
LABEL = "LABEL"
NUMBER = "NUMBER"
FORMULA = "FORMULA"

KINDS = (EMPTY, LABEL, NUMBER, FORMULA)

# Label-prefix characters (1-2-3 alignment control), exactly as in 1-2-3.
LABEL_PREFIXES = {"'": "left", "^": "center", '"': "right"}
ALIGNMENTS = ("left", "center", "right")

# An entry beginning with one of these becomes NUMBER or FORMULA (VALUE mode).
VALUE_START = frozenset("0123456789+-.(@$#")


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

def col_letters(col: int) -> str:
    """0-based column index -> letters (0 -> 'A', 255 -> 'IV')."""
    if not 0 <= col < MAX_COLS:
        raise ValueError(f"column out of range: {col}")
    letters = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def col_index(letters: str) -> int:
    """Column letters -> 0-based index ('A' -> 0, 'IV' -> 255)."""
    letters = letters.upper()
    if not letters or not letters.isalpha() or len(letters) > 3:
        raise ValueError(f"bad column letters: {letters!r}")
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    if not 1 <= n <= MAX_COLS:
        raise ValueError(f"column out of range: {letters!r}")
    return n - 1


def parse_address(text: str) -> tuple[int, int]:
    """Parse 'A1' / '$A$1' -> (col, row), both 0-based. Raises ValueError."""
    s = text.strip().replace("$", "").upper()
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    if i == 0 or i >= len(s) or not s[i:].isdigit():
        raise ValueError(f"bad cell address: {text!r}")
    col = col_index(s[:i])
    row = int(s[i:]) - 1
    if not 0 <= row < MAX_ROWS:
        raise ValueError(f"row out of range: {text!r}")
    return col, row


def format_address(col: int, row: int) -> str:
    """(col, row) 0-based -> 'A1'."""
    return f"{col_letters(col)}{row + 1}"


def is_valid_address(text: str) -> bool:
    try:
        parse_address(text)
        return True
    except ValueError:
        return False


def split_range(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Parse 'A1..B2' or 'A1' -> normalized ((c1,r1),(c2,r2)) 0-based corners."""
    parts = text.strip().split("..")
    if len(parts) == 1:
        c, r = parse_address(parts[0])
        return (c, r), (c, r)
    if len(parts) != 2:
        raise ValueError(f"bad range: {text!r}")
    a = parse_address(parts[0])
    b = parse_address(parts[1])
    return normalize_corners(a, b)


def normalize_corners(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    (c1, r1), (c2, r2) = a, b
    if c1 > c2:
        c1, c2 = c2, c1
    if r1 > r2:
        r1, r2 = r2, r1
    return (c1, r1), (c2, r2)


def format_range(corner1: tuple[int, int], corner2: tuple[int, int]) -> str:
    (c1, r1), (c2, r2) = normalize_corners(corner1, corner2)
    if (c1, r1) == (c2, r2):
        return format_address(c1, r1)
    return f"{format_address(c1, r1)}..{format_address(c2, r2)}"


def range_cells(corner1: tuple[int, int], corner2: tuple[int, int]):
    """Iterate (col, row) keys of a range, row-major."""
    (c1, r1), (c2, r2) = normalize_corners(corner1, corner2)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield c, r


# ---------------------------------------------------------------------------
# Entry classification
# ---------------------------------------------------------------------------

def parse_number_literal(text: str):
    """Parse a numeric literal, honouring the 1-2-3 percent suffix (50% -> 0.5).

    Returns float or None if not a plain number.
    """
    s = text.strip()
    if not s:
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    try:
        v = float(s)
    except ValueError:
        return None
    if pct:
        v = v / 100.0
    return v


def classify_entry(raw: str | None) -> tuple[str, str | None, float | None]:
    """Classify a raw entry string -> (kind, align, numeric_value).

    - empty            -> (EMPTY, None, None)
    - ' ^ \" prefix     -> (LABEL, alignment, text-without-prefix)
    - numeric literal  -> (NUMBER, None, value)   (percent suffix handled)
    - other value-start-> (FORMULA, None, None)
    - anything else    -> (LABEL, 'left', raw)
    """
    if raw is None or raw == "":
        return EMPTY, None, None
    first = raw[0]
    if first in LABEL_PREFIXES:
        return LABEL, LABEL_PREFIXES[first], None
    if first in VALUE_START:
        v = parse_number_literal(raw)
        if v is not None:
            return NUMBER, None, v
        return FORMULA, None, None
    return LABEL, "left", None


# ---------------------------------------------------------------------------
# Cell / Worksheet
# ---------------------------------------------------------------------------

class Cell:
    """One worksheet cell.

    raw:       exactly what the user typed (None = empty)
    kind:      EMPTY | LABEL | NUMBER | FORMULA
    value:     computed value (float for NUMBER/FORMULA, str for LABEL, None empty)
    fmt:       format code dict or None (see fmt.py)
    protected: protection flag
    hidden:    hidden (formula not shown) flag
    align:     label alignment override ('left'/'center'/'right') or None
    """

    __slots__ = ("raw", "kind", "value", "fmt", "protected", "hidden", "align")

    def __init__(self, raw: str | None = None, kind: str = EMPTY, value=None,
                 fmt=None, protected: bool = False, hidden: bool = False,
                 align: str | None = None):
        self.raw = raw
        self.kind = kind
        self.value = value
        self.fmt = fmt
        self.protected = protected
        self.hidden = hidden
        self.align = align

    @property
    def is_empty(self) -> bool:
        return self.kind == EMPTY or self.raw is None

    def to_dict(self, col: int, row: int) -> dict:
        d = {"c": col, "r": row}
        if self.raw is not None:
            d["raw"] = self.raw
        if self.kind != EMPTY:
            d["kind"] = self.kind
        if self.value is not None and not isinstance(self.value, str):
            d["value"] = self.value
        elif self.kind == LABEL and self.value is not None:
            d["value"] = self.value
        if self.fmt is not None:
            d["fmt"] = self.fmt
        if self.protected:
            d["protected"] = True
        if self.hidden:
            d["hidden"] = True
        if self.align is not None:
            d["align"] = self.align
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Cell":
        return cls(
            raw=d.get("raw"),
            kind=d.get("kind", EMPTY),
            value=d.get("value"),
            fmt=d.get("fmt"),
            protected=bool(d.get("protected", False)),
            hidden=bool(d.get("hidden", False)),
            align=d.get("align"),
        )

    def copy(self) -> "Cell":
        return Cell(self.raw, self.kind, self.value, self.fmt,
                    self.protected, self.hidden, self.align)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"Cell(raw={self.raw!r}, kind={self.kind}, value={self.value!r}, "
                f"fmt={self.fmt!r})")


class Worksheet:
    """A single worksheet. Cells are keyed (col, row), both 0-based."""

    def __init__(self, name: str = "A"):
        self.name = name
        self.cells: dict[tuple[int, int], Cell] = {}
        self.widths: dict[int, int] = {}
        self.global_fmt = None        # worksheet-global format code or None
        self.protection = False       # /WORKSHEET GLOBAL PROTECTION
        self.recalc_mode = "auto"     # 'auto' | 'manual'  (engine mirrors this)
        self.iterations = 0           # RECALCULATION ITERATION count
        self.label_prefix = "'"       # GLOBAL LABEL-PREFIX
        self.col_prefix = "^"         # GLOBAL COL-PREFIX (unused for storage)
        self.zero_suppress = False    # GLOBAL ZERO-SUPPRESS

    # -- cell access --------------------------------------------------------

    def cell(self, col: int, row: int) -> Cell | None:
        return self.cells.get((col, row))

    def get_or_make(self, col: int, row: int) -> Cell:
        key = (col, row)
        cell = self.cells.get(key)
        if cell is None:
            cell = Cell()
            self.cells[key] = cell
        return cell

    def set_cell(self, col: int, row: int, cell: Cell) -> None:
        key = (col, row)
        if cell.is_empty:
            self.cells.pop(key, None)
        else:
            self.cells[key] = cell

    def clear_cell(self, col: int, row: int) -> None:
        self.cells.pop((col, row), None)

    def raw_at(self, col: int, row: int):
        cell = self.cells.get((col, row))
        return cell.raw if cell is not None else None

    def value_at(self, col: int, row: int):
        cell = self.cells.get((col, row))
        return cell.value if cell is not None else None

    def kind_at(self, col: int, row: int) -> str:
        cell = self.cells.get((col, row))
        return cell.kind if cell is not None else EMPTY

    def set_entry(self, col: int, row: int, raw: str | None):
        """Store a raw user entry, classifying it. Returns the Cell.

        FORMULA cells get value None until the engine evaluates them.
        """
        if not (0 <= col < MAX_COLS and 0 <= row < MAX_ROWS):
            raise ValueError(f"cell out of range: col={col} row={row}")
        kind, align, num = classify_entry(raw)
        if kind == EMPTY:
            self.clear_cell(col, row)
            return Cell()
        if kind == LABEL:
            text = raw[1:] if raw[0] in LABEL_PREFIXES else raw
            cell = Cell(raw=raw, kind=LABEL, value=text, align=align)
        elif kind == NUMBER:
            cell = Cell(raw=raw, kind=NUMBER, value=num, align=align)
        else:
            cell = Cell(raw=raw, kind=FORMULA, value=None, align=align)
        old = self.cells.get((col, row))
        if old is not None:
            cell.fmt = old.fmt
            cell.protected = old.protected
            cell.hidden = old.hidden
        self.cells[(col, row)] = cell
        return cell

    # -- columns ------------------------------------------------------------

    def col_width(self, col: int) -> int:
        return self.widths.get(col, DEFAULT_COL_WIDTH)

    def set_col_width(self, col: int, width: int) -> None:
        width = max(0, min(MAX_COL_WIDTH, int(width)))
        if width == DEFAULT_COL_WIDTH:
            self.widths.pop(col, None)
        else:
            self.widths[col] = width

    def reset_col_widths(self) -> None:
        self.widths.clear()

    # -- structural edits ---------------------------------------------------

    def _shift_cells(self, dc: int, dr: int, predicate) -> None:
        new_cells: dict[tuple[int, int], Cell] = {}
        for (c, r), cell in self.cells.items():
            if predicate(c, r):
                nc, nr = c + dc, r + dr
                if 0 <= nc < MAX_COLS and 0 <= nr < MAX_ROWS:
                    new_cells[(nc, nr)] = cell
            else:
                new_cells[(c, r)] = cell
        self.cells = new_cells

    def _shift_widths_cols(self, col: int, n: int, drop=None) -> None:
        new_widths: dict[int, int] = {}
        for c, w in self.widths.items():
            if drop is not None and drop[0] <= c < drop[1]:
                continue
            nc = c + n if c >= col else c
            if 0 <= nc < MAX_COLS:
                new_widths[nc] = w
        self.widths = new_widths

    def insert_cols(self, col: int, n: int = 1) -> None:
        self._shift_cells(n, 0, lambda c, r: c >= col)
        self._shift_widths_cols(col, n)

    def delete_cols(self, col: int, n: int = 1) -> None:
        self._shift_cells(-n, 0, lambda c, r: c >= col + n)
        self.cells = {k: v for k, v in self.cells.items() if not (col <= k[0] < col + n)}
        self._shift_widths_cols(col + n, -n, drop=(col, col + n))

    def insert_rows(self, row: int, n: int = 1) -> None:
        self._shift_cells(0, n, lambda c, r: r >= row)

    def delete_rows(self, row: int, n: int = 1) -> None:
        self._shift_cells(0, -n, lambda c, r: r >= row + n)
        self.cells = {k: v for k, v in self.cells.items() if not (row <= k[1] < row + n)}

    # -- iteration ----------------------------------------------------------

    def iter_range(self, corner1: tuple[int, int], corner2: tuple[int, int]):
        """Yield (col, row, cell-or-None) row-major over a normalized range."""
        for c, r in range_cells(corner1, corner2):
            yield c, r, self.cells.get((c, r))

    def used_bounds(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Smallest range covering all non-empty cells, or ((0,0),(0,0))."""
        if not self.cells:
            return (0, 0), (0, 0)
        cs = [k[0] for k in self.cells]
        rs = [k[1] for k in self.cells]
        return (min(cs), min(rs)), (max(cs), max(rs))
