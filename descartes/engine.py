"""Formula engine: parser, compiler, dependency graph, recalculation.

Syntax matches 1-2-3 Release 3.1 (see reference manual). Precedence, high to
low: unary +/-, ^, * /, + -, comparisons (= <> < > <= >=), #AND# #OR#, &
(string concatenation, lowest).
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import functions as F
from .grid import (MAX_COLS, MAX_ROWS, col_index, col_letters, normalize_corners)


class FormulaSyntaxError(Exception):
    pass


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_OPS2 = ("<=", ">=", "<>")
_OPS1 = set("+-*/^&=<>")
_LOGIC = {"#AND#": "AND", "#OR#": "OR", "#NOT#": "NOT"}


class Token:
    __slots__ = ("kind", "value", "text")

    def __init__(self, kind, value=None, text=""):
        self.kind = kind
        self.value = value
        self.text = text

    def __repr__(self):  # pragma: no cover
        return f"Token({self.kind}, {self.value!r})"


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in " \t":
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and s[i + 1].isdigit()):
            j = i
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            if j < n and s[j] in "eE" and j + 1 < n and (s[j + 1].isdigit() or s[j + 1] in "+-"):
                j += 2
                while j < n and s[j].isdigit():
                    j += 1
            text = s[i:j]
            try:
                value = float(text)
            except ValueError:
                raise FormulaSyntaxError(f"bad number {text!r}")
            toks.append(Token("NUM", value, text))
            i = j
            continue
        if ch == '"':
            j = i + 1
            buf = []
            while j < n and s[j] != '"':
                buf.append(s[j])
                j += 1
            if j >= n:
                raise FormulaSyntaxError("unterminated string")
            toks.append(Token("STR", "".join(buf), s[i:j + 1]))
            i = j + 1
            continue
        if ch == "@":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            if j == i + 1:
                raise FormulaSyntaxError("bad @function")
            toks.append(Token("FUNC", s[i + 1:j].upper(), s[i:j]))
            i = j
            continue
        if ch == "$" or ch.isalpha():
            j = i
            abs_c = False
            if s[j] == "$":
                abs_c = True
                j += 1
            k = j
            while k < n and s[k].isalpha():
                k += 1
            if k == j:
                raise FormulaSyntaxError(f"bad reference at {s[i:i+8]!r}")
            letters = s[j:k].upper()
            j = k
            sheets = None
            if j < n and s[j] == "!":
                sheets = (letters, letters)
                j += 1
            elif j < n and s[j] == ":":
                k2 = j + 1
                if k2 < n and s[k2] == "$":
                    k2 += 1
                k3 = k2
                while k3 < n and s[k3].isalpha():
                    k3 += 1
                if k3 == k2 or k3 >= n or s[k3] != "!":
                    raise FormulaSyntaxError(f"bad sheet range at {s[i:i+8]!r}")
                sheets = (letters, s[k2:k3].upper())
                j = k3 + 1
            if sheets is not None:
                if j < n and s[j] == "$":
                    abs_c = True
                    j += 1
                k = j
                while k < n and s[k].isalpha():
                    k += 1
                if k == j:
                    raise FormulaSyntaxError(f"bad reference at {s[i:i+8]!r}")
                letters = s[j:k].upper()
                j = k
            if sheets is None and not (j < n and (s[j].isdigit() or s[j] == "$")):
                toks.append(Token("NAME", letters, s[i:j]))
                i = j
                continue
            abs_r = False
            if j < n and s[j] == "$":
                abs_r = True
                j += 1
            k = j
            while k < n and s[k].isdigit():
                k += 1
            if k < n and s[k].isalpha():
                k2 = i
                while k2 < n and (s[k2].isalnum() or s[k2] == "$"):
                    k2 += 1
                toks.append(Token("NAME", s[i:k2].upper(), s[i:k2]))
                i = k2
                continue
            if k == j:
                raise FormulaSyntaxError(f"bad reference at {s[i:i+8]!r}")
            digits = s[j:k]
            try:
                col = col_index(letters)
                row = int(digits) - 1
            except ValueError as e:
                raise FormulaSyntaxError(str(e))
            if not 0 <= row < MAX_ROWS:
                raise FormulaSyntaxError(f"row out of range: {digits}")
            toks.append(Token("CELL", (col, row, abs_c, abs_r, sheets), s[i:k]))
            i = k
            continue
        if ch == "#":
            j = i
            while j < n and s[j] != "#" or j == i:
                j += 1
            word = s[i:j + 1].upper() if j < n else s[i:].upper()
            if word in _LOGIC:
                toks.append(Token(_LOGIC[word], word))
                i = j + 1
                continue
            raise FormulaSyntaxError(f"bad operator {word!r}")
        two = s[i:i + 2]
        if two in _OPS2:
            toks.append(Token("OP", two, two))
            i += 2
            continue
        if ch in _OPS1:
            toks.append(Token("OP", ch, ch))
            i += 1
            continue
        if ch == "(":
            toks.append(Token("LPAREN", ch, ch))
            i += 1
            continue
        if ch == ")":
            toks.append(Token("RPAREN", ch, ch))
            i += 1
            continue
        if ch == ",":
            toks.append(Token("COMMA", ch, ch))
            i += 1
            continue
        if two == "..":
            toks.append(Token("DOTDOT", two, two))
            i += 2
            continue
        if ch == "%":
            toks.append(Token("PCT", ch, ch))
            i += 1
            continue
        if ch == "&":
            toks.append(Token("OP", "&", "&"))
            i += 1
            continue
        raise FormulaSyntaxError(f"unexpected character {ch!r}")
    toks.append(Token("EOF"))
    return toks


# ---------------------------------------------------------------------------
# Parser (recursive descent) -> AST tuples
# ---------------------------------------------------------------------------

class _Parser:
    MAX_DEPTH = 100

    def __init__(self, toks):
        self.toks = toks
        self.pos = 0
        self.depth = 0

    def peek(self) -> Token:
        return self.toks[self.pos]

    def next(self) -> Token:
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, kind) -> Token:
        t = self.peek()
        if t.kind != kind:
            raise FormulaSyntaxError(f"expected {kind}, got {t.kind} ({t.text!r})")
        return self.next()

    def parse(self):
        node = self.concat()
        if self.peek().kind != "EOF":
            raise FormulaSyntaxError(f"unexpected {self.peek().text!r}")
        return node

    def concat(self):
        node = self.andor()
        while self.peek().kind == "OP" and self.peek().value == "&":
            self.next()
            node = ("bin", "&", node, self.andor())
        return node

    def andor(self):
        node = self.compare()
        while self.peek().kind in ("AND", "OR"):
            op = self.next().kind
            node = ("bin", op, node, self.compare())
        return node

    def compare(self):
        node = self.add()
        while self.peek().kind == "OP" and self.peek().value in ("=", "<>", "<", ">", "<=", ">="):
            op = self.next().value
            node = ("bin", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek().kind == "OP" and self.peek().value in ("+", "-"):
            op = self.next().value
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.pow()
        while self.peek().kind == "OP" and self.peek().value in ("*", "/"):
            op = self.next().value
            node = ("bin", op, node, self.pow())
        return node

    def pow(self):
        node = self.unary()
        while self.peek().kind == "OP" and self.peek().value == "^":
            self.next()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        t = self.peek()
        if t.kind == "OP" and t.value in ("+", "-"):
            self.next()
            return ("un", t.value, self.unary())
        if t.kind == "NOT":
            self.next()
            return ("un", "#NOT#", self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek().kind == "PCT":
            self.next()
            node = ("pct", node)
        return node

    def primary(self):
        t = self.peek()
        if t.kind == "NUM":
            self.next()
            return ("num", t.value, t.text)
        if t.kind == "STR":
            self.next()
            return ("str", t.value)
        if t.kind == "NAME":
            self.next()
            return ("name", t.value)
        if t.kind == "CELL":
            self.next()
            c, r, ac, ar, sheets = t.value
            if self.peek().kind == "DOTDOT":
                self.next()
                t2 = self.expect("CELL")
                c2, r2, ac2, ar2, sheets2 = t2.value
                (c1, r1), (c2, r2) = normalize_corners((c, r), (c2, r2))
                sh = sheets or sheets2
                if sh:
                    return ("range3d", sh, (c1, r1, ac, ar), (c2, r2, ac2, ar2))
                return ("range", (c1, r1, ac, ar), (c2, r2, ac2, ar2))
            if sheets:
                return ("cell3d", sheets, c, r, ac, ar)
            return ("cell", c, r, ac, ar)
        if t.kind == "FUNC":
            self.next()
            args = []
            if self.peek().kind == "LPAREN":
                self.next()
                if self.peek().kind != "RPAREN":
                    args.append(self.concat())
                    while self.peek().kind == "COMMA":
                        self.next()
                        args.append(self.concat())
                self.expect("RPAREN")
            return ("func", t.value, args)
        if t.kind == "LPAREN":
            self.next()
            self.depth += 1
            if self.depth > self.MAX_DEPTH:
                raise FormulaSyntaxError("formula too deeply nested")
            node = self.concat()
            self.depth -= 1
            self.expect("RPAREN")
            return node
        raise FormulaSyntaxError(f"unexpected {t.text or t.kind!r}")


def parse(raw: str):
    """Parse formula text -> AST. A single leading '+' is the 1-2-3 formula
    indicator (not a unary operator) and is stripped."""
    if raw.startswith("+"):
        raw = raw[1:]
    return _Parser(tokenize(raw)).parse()


# ---------------------------------------------------------------------------
# AST utilities: refs, serialization, shifting (for /COPY and /MOVE)
# ---------------------------------------------------------------------------

def collect_refs(node, refs: set | None = None, ranges: list | None = None):
    """-> (set of (col,row) single-cell refs, list of normalized range corners)"""
    if refs is None:
        refs = set()
    if ranges is None:
        ranges = []
    kind = node[0]
    if kind == "cell":
        refs.add((node[1], node[2]))
    elif kind == "range":
        (c1, r1, _, _), (c2, r2, _, _) = node[1], node[2]
        ranges.append(((c1, r1), (c2, r2)))
    elif kind == "bin":
        collect_refs(node[2], refs, ranges)
        collect_refs(node[3], refs, ranges)
    elif kind == "un":
        collect_refs(node[2], refs, ranges)
    elif kind == "pct":
        collect_refs(node[1], refs, ranges)
    elif kind == "func":
        for a in node[2]:
            collect_refs(a, refs, ranges)
    return refs, ranges


def collect_xrefs(node, xrefs: list | None = None, xranges: list | None = None):
    """3-D (sheet-qualified) refs -> (list of (s1, s2, col, row),
    list of (s1, s2, corner1, corner2)) for cross-sheet dependency tracking."""
    if xrefs is None:
        xrefs = []
    if xranges is None:
        xranges = []
    kind = node[0]
    if kind == "cell3d":
        xrefs.append((node[1][0], node[1][1], node[2], node[3]))
    elif kind == "range3d":
        (c1, r1, _, _), (c2, r2, _, _) = node[2], node[3]
        xranges.append((node[1][0], node[1][1], (c1, r1), (c2, r2)))
    elif kind == "bin":
        collect_xrefs(node[2], xrefs, xranges)
        collect_xrefs(node[3], xrefs, xranges)
    elif kind == "un":
        collect_xrefs(node[2], xrefs, xranges)
    elif kind == "pct":
        collect_xrefs(node[1], xrefs, xranges)
    elif kind == "func":
        for a in node[2]:
            collect_xrefs(a, xrefs, xranges)
    return xrefs, xranges


def _sheet_prefix(sheets) -> str:
    s1, s2 = sheets
    return f"{s1}!" if s1 == s2 else f"{s1}:{s2}!"


def span_names(s1, s2, order):
    """Sheet names spanned by s1:s2 given the workbook sheet order."""
    if s1 not in order or s2 not in order:
        return [s1]
    i, j = order.index(s1), order.index(s2)
    if i > j:
        i, j = j, i
    return order[i:j + 1]


def narrow_sheets(node, deleted, order, own):
    """Rewrite 3-D refs in an AST after `deleted` was removed from the
    workbook. `own` is the sheet of the formula being rewritten: a ref whose
    span collapses to that sheet becomes 2-D, to another single sheet stays
    sheet-qualified, and a ref that loses its whole span becomes @ERR()."""
    kind = node[0]
    if kind == "cell3d":
        rest = [s for s in span_names(node[1][0], node[1][1], order) if s != deleted]
        if not rest:
            return ("func", "ERR", [])
        if len(rest) == 1 and rest[0] == own:
            return ("cell", node[2], node[3], node[4], node[5])
        return ("cell3d", (rest[0], rest[-1]), node[2], node[3], node[4], node[5])
    if kind == "range3d":
        rest = [s for s in span_names(node[1][0], node[1][1], order) if s != deleted]
        if not rest:
            return ("func", "ERR", [])
        if len(rest) == 1 and rest[0] == own:
            return ("range", node[2], node[3])
        return ("range3d", (rest[0], rest[-1]), node[2], node[3])
    if kind == "bin":
        return ("bin", node[1], narrow_sheets(node[2], deleted, order, own),
                narrow_sheets(node[3], deleted, order, own))
    if kind == "un":
        return ("un", node[1], narrow_sheets(node[2], deleted, order, own))
    if kind == "pct":
        return ("pct", narrow_sheets(node[1], deleted, order, own))
    if kind == "func":
        return ("func", node[1], [narrow_sheets(a, deleted, order, own) for a in node[2]])
    return node


def collect_names(node, names: list | None = None):
    """-> list of range-name references in the AST (resolved at register/eval)."""
    if names is None:
        names = []
    kind = node[0]
    if kind == "name":
        names.append(node[1])
    elif kind == "bin":
        collect_names(node[2], names)
        collect_names(node[3], names)
    elif kind == "un":
        collect_names(node[2], names)
    elif kind == "pct":
        collect_names(node[1], names)
    elif kind == "func":
        for a in node[2]:
            collect_names(a, names)
    return names


def _ser_cell(c, r, ac, ar) -> str:
    return ("$" if ac else "") + col_letters(c) + ("$" if ar else "") + str(r + 1)


def serialize(node) -> str:
    kind = node[0]
    if kind == "num":
        return node[2]
    if kind == "str":
        return '"' + node[1].replace('"', '""') + '"'
    if kind == "name":
        return node[1]
    if kind == "cell":
        return _ser_cell(node[1], node[2], node[3], node[4])
    if kind == "cell3d":
        return _sheet_prefix(node[1]) + _ser_cell(node[2], node[3], node[4], node[5])
    if kind == "range":
        return _ser_cell(*node[1]) + ".." + _ser_cell(*node[2])
    if kind == "range3d":
        return _sheet_prefix(node[1]) + _ser_cell(*node[2]) + ".." + _ser_cell(*node[3])
    if kind == "bin":
        op = node[1]
        op_text = {"AND": "#AND#", "OR": "#OR#"}.get(op, op)
        return "(" + serialize(node[2]) + op_text + serialize(node[3]) + ")"
    if kind == "un":
        return "(" + node[1] + serialize(node[1 + 1]) + ")"
    if kind == "pct":
        return "(" + serialize(node[1]) + "%)"
    if kind == "func":
        return "@" + node[1] + "(" + ",".join(serialize(a) for a in node[2]) + ")"
    raise FormulaSyntaxError(f"cannot serialize {kind}")


def _shift_corner(corner, dc, dr):
    c, r, ac, ar = corner
    return (c if ac else min(max(c + dc, 0), MAX_COLS - 1),
            r if ar else min(max(r + dr, 0), MAX_ROWS - 1), ac, ar)


def shift(node, dc: int, dr: int):
    """Return a copy of the AST with relative refs shifted by (dc, dr).

    Absolute ($-marked) components are preserved. Sheet spans are kept.
    """
    kind = node[0]
    if kind in ("num", "str", "name"):
        return node
    if kind == "cell":
        return ("cell",) + _shift_corner(node[1:], dc, dr)
    if kind == "cell3d":
        return ("cell3d", node[1], *_shift_corner(node[2:], dc, dr))
    if kind == "range":
        return ("range", _shift_corner(node[1], dc, dr), _shift_corner(node[2], dc, dr))
    if kind == "range3d":
        return ("range3d", node[1], _shift_corner(node[2], dc, dr),
                _shift_corner(node[3], dc, dr))
    if kind == "bin":
        return ("bin", node[1], shift(node[2], dc, dr), shift(node[3], dc, dr))
    if kind == "un":
        return ("un", node[1], shift(node[2], dc, dr))
    if kind == "pct":
        return ("pct", shift(node[1], dc, dr))
    if kind == "func":
        return ("func", node[1], [shift(a, dc, dr) for a in node[2]])
    raise FormulaSyntaxError(f"cannot shift {kind}")


def shift_raw(raw: str, dc: int, dr: int) -> str:
    """Shift a formula's raw text for /COPY and /MOVE. Preserves leading '+'."""
    ast = parse(raw)
    out = serialize(shift(ast, dc, dr))
    # strip one redundant outer paren pair so results read like 1-2-3 (+B1*2)
    if out.startswith("(") and out.endswith(")"):
        depth = 0
        wraps = True
        for i, ch in enumerate(out):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(out) - 1:
                    wraps = False
                    break
        if wraps and depth == 0:
            out = out[1:-1]
    if raw.startswith("+"):
        out = "+" + out
    return out


def adjust_refs(node, axis: str, pos: int, delta: int, delete: bool = False):
    """Adjust every reference for /WORKSHEET INSERT (delta > 0) or DELETE
    (delta < 0, band [pos, pos - delta)). Unlike copy-shift, absolute ($)
    refs are adjusted too, as 1-2-3 does. Range corners adjust independently,
    so ranges spanning pos expand/contract. A single-cell ref inside a deleted
    band becomes @ERR()."""
    maxv = MAX_COLS - 1 if axis == "col" else MAX_ROWS - 1

    def adj(v):
        if not delete:
            return min(v + delta, maxv) if v >= pos else v
        if v >= pos - delta:
            return max(0, v + delta)
        if v >= pos:
            return None
        return v

    def adj_corner(corner):
        c, r, ac, ar = corner
        nv = adj(c if axis == "col" else r)
        if nv is None:
            nv = pos
        if axis == "col":
            return (nv, r, ac, ar)
        return (c, nv, ac, ar)

    kind = node[0]
    if kind == "name":
        return node
    if kind == "cell":
        c, r, ac, ar = node[1], node[2], node[3], node[4]
        nv = adj(c if axis == "col" else r)
        if nv is None:
            return ("func", "ERR", [])
        if axis == "col":
            return ("cell", nv, r, ac, ar)
        return ("cell", c, nv, ac, ar)
    if kind == "range":
        return ("range", adj_corner(node[1]), adj_corner(node[2]))
    if kind == "cell3d":
        c, r, ac, ar = node[2], node[3], node[4], node[5]
        nv = adj(c if axis == "col" else r)
        if nv is None:
            return ("func", "ERR", [])
        if axis == "col":
            return ("cell3d", node[1], nv, r, ac, ar)
        return ("cell3d", node[1], c, nv, ac, ar)
    if kind == "range3d":
        return ("range3d", node[1], adj_corner(node[2]), adj_corner(node[3]))
    if kind == "bin":
        return ("bin", node[1], adjust_refs(node[2], axis, pos, delta, delete),
                adjust_refs(node[3], axis, pos, delta, delete))
    if kind == "un":
        return ("un", node[1], adjust_refs(node[2], axis, pos, delta, delete))
    if kind == "pct":
        return ("pct", adjust_refs(node[1], axis, pos, delta, delete))
    if kind == "func":
        return ("func", node[1], [adjust_refs(a, axis, pos, delta, delete) for a in node[2]])
    return node


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class RangeValue:
    """A range used inside a formula. Lazy; resolves to scalars on demand.
    `sheet` pins the range to another sheet (named range on sheet B)."""

    __slots__ = ("ctx", "c1", "r1", "c2", "r2", "sheet")

    def __init__(self, ctx, c1, r1, c2, r2, sheet=None):
        self.ctx = ctx
        self.c1, self.r1, self.c2, self.r2 = c1, r1, c2, r2
        self.sheet = sheet

    @property
    def corners(self):
        return self.c1, self.r1, self.c2, self.r2

    def cells(self):
        for r in range(self.r1, self.r2 + 1):
            for c in range(self.c1, self.c2 + 1):
                yield c, r

    def _val(self, ctx, c, r):
        if self.sheet is not None:
            return ctx.val_sheet(self.sheet, c, r)
        return ctx.val(c, r)

    def values(self):
        for c, r in self.cells():
            yield self._val(self.ctx, c, r)

    def scalar(self, ctx=None):
        ctx = ctx or self.ctx
        fc, fr = ctx.fkey
        c1, r1, c2, r2 = self.c1, self.r1, self.c2, self.r2
        if c1 <= fc <= c2:
            row = fr if r1 <= fr <= r2 else (r1 if fr < r1 else r2)
            return self._val(ctx, fc, row)
        if r1 <= fr <= r2:
            col = fc if c1 <= fc <= c2 else (c1 if fc < c1 else c2)
            return self._val(ctx, col, fr)
        return self._val(ctx, c1, r1)

    def __repr__(self):  # pragma: no cover
        return f"RangeValue({col_letters(self.c1)}{self.r1+1}..{col_letters(self.c2)}{self.r2+1})"


class Range3D:
    """A sheet-spanning range (A:C!A1..B2). Aggregates across the sheets in the
    span; in scalar context resolves like 1-2-3: the active sheet if it is in
    the span, otherwise the first sheet of the span."""

    __slots__ = ("ctx", "s1", "s2", "c1", "r1", "c2", "r2")

    def __init__(self, ctx, s1, s2, c1, r1, c2, r2):
        self.ctx = ctx
        self.s1, self.s2 = s1, s2
        self.c1, self.r1, self.c2, self.r2 = c1, r1, c2, r2

    @property
    def corners(self):
        return self.c1, self.r1, self.c2, self.r2

    def sheets(self):
        wb = getattr(self.ctx.engine, "wb", None)
        if wb is None:
            return [self.s1]
        return wb.span(self.s1, self.s2)

    def cells(self):
        wb = self.ctx.engine.wb
        for sn in self.sheets():
            ws = wb.sheets.get(sn)
            if ws is None:
                continue
            for r in range(self.r1, self.r2 + 1):
                for c in range(self.c1, self.c2 + 1):
                    yield sn, c, r

    def values(self):
        wb = self.ctx.engine.wb
        for sn, c, r in self.cells():
            cell = wb.sheets[sn].cells.get((c, r))
            yield cell.value if cell is not None else None

    def scalar(self, ctx=None):
        ctx = ctx or self.ctx
        return ctx.val3d(self.s1, self.s2, self.c1, self.r1)

    def __repr__(self):  # pragma: no cover
        return f"Range3D({self.s1}:{self.s2}!{self.c1},{self.r1}..{self.c2},{self.r2})"


class RefVal:
    """A cell/range reference passed structurally (for @CELL)."""

    __slots__ = ("c", "r")
    _is_ref = True

    def __init__(self, c, r):
        self.c = c
        self.r = r


class Ctx:
    __slots__ = ("engine", "fkey")

    def __init__(self, engine, fkey):
        self.engine = engine
        self.fkey = fkey

    def val(self, c, r):
        return self.engine.value(c, r)

    def val_sheet(self, sheet, c, r):
        wb = getattr(self.engine, "wb", None)
        if wb is None or sheet is None or sheet == wb.active:
            return self.engine.value(c, r)
        ws = wb.sheets.get(sheet)
        if ws is None:
            return None
        cell = ws.cells.get((c, r))
        return cell.value if cell is not None else None

    def resolve_name(self, name):
        from .functions import ERR
        wb = getattr(self.engine, "wb", None)
        names = getattr(wb, "names", None) if wb is not None else None
        entry = names.get(name.upper()) if names else None
        if entry is None:
            return ERR
        (c1, r1), (c2, r2) = entry["range"]
        sheets = entry.get("sheets")
        if sheets:
            return Range3D(self, sheets[0], sheets[1], c1, r1, c2, r2)
        sheet = entry.get("sheet")
        return RangeValue(self, c1, r1, c2, r2, sheet=sheet)

    def val3d(self, s1, s2, c, r):
        wb = getattr(self.engine, "wb", None)
        if wb is None:
            return self.engine.value(c, r)
        names = wb.span(s1, s2)
        pick = wb.active if wb.active in names else names[0]
        ws = wb.sheets.get(pick)
        if ws is None:
            return None
        cell = ws.cells.get((c, r))
        return cell.value if cell is not None else None


def _scalar(v, ctx):
    if isinstance(v, RangeValue):
        return v.scalar(ctx)
    return v


def _apply_bin(op, a, b, ctx):
    a = _scalar(a, ctx)
    b = _scalar(b, ctx)
    if isinstance(a, F.Error):
        return a
    if isinstance(b, F.Error):
        return b
    if op in ("=", "<>", "<", ">", "<=", ">="):
        try:
            c = F.lotus_compare(a, b)
        except F._Propagate as p:
            return p.err
        if op == "=":
            r = c == 0
        elif op == "<>":
            r = c != 0
        elif op == "<":
            r = c < 0
        elif op == ">":
            r = c > 0
        elif op == "<=":
            r = c <= 0
        else:
            r = c >= 0
        return 1.0 if r else 0.0
    if op == "&":
        return F.to_text(a) + F.to_text(b)
    if op == "AND":
        x = F.to_num(a)
        y = F.to_num(b)
        if isinstance(x, F.Error):
            return x
        if isinstance(y, F.Error):
            return y
        return 1.0 if (x != 0 and y != 0) else 0.0
    if op == "OR":
        x = F.to_num(a)
        y = F.to_num(b)
        if isinstance(x, F.Error):
            return x
        if isinstance(y, F.Error):
            return y
        return 1.0 if (x != 0 or y != 0) else 0.0
    x = F.to_num(a)
    y = F.to_num(b)
    if isinstance(x, F.Error):
        return x
    if isinstance(y, F.Error):
        return y
    if op in ("+", "-", "*"):
        r = {"+": x + y, "-": x - y, "*": x * y}[op]
        return r if math.isfinite(r) else F.ERR
    if op == "/":
        if y == 0:
            return F.ERR
        r = x / y
        return r if math.isfinite(r) else F.ERR
    if op == "^":
        try:
            r = x ** y
        except (OverflowError, ZeroDivisionError):
            return F.ERR
        if isinstance(r, complex):
            return F.ERR
        return float(r)
    raise FormulaSyntaxError(f"bad operator {op}")


def compile_node(node):
    kind = node[0]
    if kind == "num":
        v = node[1]
        return lambda ctx: v
    if kind == "str":
        v = node[1]
        return lambda ctx: v
    if kind == "cell":
        c, r = node[1], node[2]
        return lambda ctx: ctx.val(c, r)
    if kind == "range":
        (c1, r1, _, _), (c2, r2, _, _) = node[1], node[2]
        return lambda ctx: RangeValue(ctx, c1, r1, c2, r2)
    if kind == "cell3d":
        s1, s2, c, r = node[1][0], node[1][1], node[2], node[3]
        return lambda ctx: ctx.val3d(s1, s2, c, r)
    if kind == "range3d":
        s1, s2 = node[1]
        (c1, r1, _, _), (c2, r2, _, _) = node[2], node[3]
        return lambda ctx: Range3D(ctx, s1, s2, c1, r1, c2, r2)
    if kind == "name":
        nm = node[1]
        return lambda ctx: ctx.resolve_name(nm)
    if kind == "bin":
        op = node[1]
        fl = compile_node(node[2])
        fr = compile_node(node[3])
        return lambda ctx: _apply_bin(op, fl(ctx), fr(ctx), ctx)
    if kind == "un":
        op = node[1]
        f = compile_node(node[2])
        if op == "-":
            return lambda ctx: _neg(f(ctx), ctx)
        if op == "#NOT#":
            return lambda ctx: _not(f(ctx), ctx)
        return lambda ctx: _pos(f(ctx), ctx)
    if kind == "pct":
        f = compile_node(node[1])
        return lambda ctx: _pct(f(ctx), ctx)
    if kind == "func":
        name = node[1]
        if name == "CELL":
            # @CELL needs the reference itself, not its value
            parts = []
            for a in node[2]:
                if a[0] == "cell":
                    parts.append(("static", RefVal(a[1], a[2])))
                elif a[0] == "range":
                    parts.append(("static", RefVal(a[1][0], a[1][1])))
                elif a[0] == "cell3d":
                    parts.append(("static", RefVal(a[2], a[3])))
                elif a[0] == "range3d":
                    parts.append(("static", RefVal(a[2][0], a[2][1])))
                else:
                    parts.append(("fn", compile_node(a)))

            def call_cell(ctx, parts=parts, name=name):
                args = [p[1] if p[0] == "static" else p[1](ctx) for p in parts]
                return F.call(name, args, ctx)
            return call_cell
        argfns = [compile_node(a) for a in node[2]]
        return lambda ctx: F.call(name, [af(ctx) for af in argfns], ctx)
    raise FormulaSyntaxError(f"cannot compile {kind}")


def _neg(v, ctx):
    v = _scalar(v, ctx)
    if isinstance(v, F.Error):
        return v
    x = F.to_num(v)
    if isinstance(x, F.Error):
        return x
    return -x


def _pos(v, ctx):
    v = _scalar(v, ctx)
    if isinstance(v, F.Error):
        return v
    return F.to_num(v)


def _not(v, ctx):
    v = _scalar(v, ctx)
    if isinstance(v, F.Error):
        return v
    x = F.to_num(v)
    if isinstance(x, F.Error):
        return x
    return 0.0 if x != 0 else 1.0


def _pct(v, ctx):
    v = _scalar(v, ctx)
    if isinstance(v, F.Error):
        return v
    x = F.to_num(v)
    if isinstance(x, F.Error):
        return x
    return x / 100.0


class Compiled:
    __slots__ = ("raw", "ast", "fn", "refs", "ranges", "xrefs", "xranges")

    def __init__(self, raw, ast):
        self.raw = raw
        self.ast = ast
        self.fn = compile_node(ast)
        self.refs, self.ranges = collect_refs(ast)
        self.xrefs, self.xranges = collect_xrefs(ast)


# ---------------------------------------------------------------------------
# Engine: dependency graph + recalculation
# ---------------------------------------------------------------------------

class Engine:
    """Owns formula compilation and recalculation for one worksheet."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.formulas: dict[tuple[int, int], Compiled] = {}
        self.dependents: dict[tuple[int, int], set] = defaultdict(set)
        self.range_dependents: list = []   # (corner1, corner2, formula_key)
        self.order: list = []              # topological order of acyclic formulas
        self.cyclic: set = set()
        self.dirty: set = set()
        self.mode = "auto"
        self.iterations = 0
        self._graph_stale = True
        self.wb = None       # workbook facade (App) for 3-D refs; None = single sheet

    # -- settings -----------------------------------------------------------

    def sync_settings(self):
        self.mode = self.sheet.recalc_mode
        self.iterations = self.sheet.iterations

    # -- registration -------------------------------------------------------

    def register(self, key) -> bool:
        """Compile the formula in cell `key` (if any). Returns True if formula."""
        cell = self.sheet.cells.get(key)
        if cell is None or cell.kind != "FORMULA" or cell.raw is None:
            self.unregister(key)
            return False
        try:
            ast = parse(cell.raw)
        except FormulaSyntaxError:
            cell.value = F.ERR
            self.formulas.pop(key, None)
            self._graph_stale = True
            return False
        comp = Compiled(cell.raw, ast)
        wb = self.wb
        if wb is not None:
            for nm in collect_names(ast):
                entry = wb.names.get(nm)
                if not entry:
                    continue
                (c1, r1), (c2, r2) = entry["range"]
                sheets = entry.get("sheets")
                if sheets:
                    comp.xranges.append((sheets[0], sheets[1], (c1, r1), (c2, r2)))
                elif entry.get("sheet") == self.sheet.name:
                    comp.ranges.append(((c1, r1), (c2, r2)))
                else:
                    sh = entry.get("sheet", "")
                    comp.xranges.append((sh, sh, (c1, r1), (c2, r2)))
        self.formulas[key] = comp
        self._graph_stale = True
        self.dirty.add(key)
        return True

    def note_edit(self, key):
        """Call after a cell's raw content changed: recompile and propagate."""
        self.register(key)
        self.mark_dirty(key)

    def unregister(self, key):
        if key in self.formulas:
            del self.formulas[key]
            self._graph_stale = True

    def register_all(self):
        self.formulas.clear()
        self._graph_stale = True
        for key, cell in list(self.sheet.cells.items()):
            if cell.kind == "FORMULA":
                self.register(key)
        self.rebuild()

    def rebuild(self):
        """Recompute the dependency graph and topological evaluation order."""
        fset = set(self.formulas)
        dependents: dict[tuple[int, int], set] = defaultdict(set)
        range_deps = []
        for key, comp in self.formulas.items():
            for ref in comp.refs:
                dependents[ref].add(key)
            for corners in comp.ranges:
                range_deps.append((corners[0], corners[1], key))
        indeg = {}
        for key, comp in self.formulas.items():
            deps = {r for r in comp.refs if r in fset}
            for (c1, c2) in comp.ranges:
                area = (c2[0] - c1[0] + 1) * (c2[1] - c1[1] + 1)
                if area <= len(fset):
                    for c, r in _range_cells_iter(c1, c2):
                        if (c, r) in fset:
                            deps.add((c, r))
                else:
                    for fk in fset:
                        if _range_contains(c1, c2, fk):
                            deps.add(fk)
            indeg[key] = len(deps)
        queue = [k for k in self.formulas if indeg[k] == 0]
        order = []
        while queue:
            k = queue.pop()
            order.append(k)
            for dep in dependents.get(k, ()):
                if dep in indeg:
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        queue.append(dep)
            for (c1, c2, fkey) in range_deps:
                if fkey in indeg and _range_contains(c1, c2, k):
                    indeg[fkey] -= 1
                    if indeg[fkey] == 0:
                        queue.append(fkey)
        cyclic = fset - set(order)
        self.dependents = dependents
        self.range_dependents = range_deps
        self.order = order
        self.cyclic = cyclic
        self._graph_stale = False

    # -- values -------------------------------------------------------------

    def value(self, c, r):
        cell = self.sheet.cells.get((c, r))
        return cell.value if cell is not None else None

    def mark_dirty(self, key):
        if self._graph_stale:
            self.rebuild()
        stack = [key]
        while stack:
            k = stack.pop()
            if k in self.dirty:
                continue
            self.dirty.add(k)
            for d in self.dependents.get(k, ()):
                stack.append(d)
            for (c1, c2, fkey) in self.range_dependents:
                if fkey not in self.dirty and _range_contains(c1, c2, k):
                    stack.append(fkey)

    def needs_calc(self) -> bool:
        return bool(self.dirty) and self.mode == "manual"

    # -- recalculation ------------------------------------------------------

    def recalc(self, force: bool = False) -> bool:
        """Recalculate dirty formulas (or all if force). Honours MANUAL mode.

        Returns True if a recalculation actually ran.
        """
        self.sync_settings()
        if self._graph_stale:
            self.rebuild()
        if self.mode == "manual" and not force:
            return False
        targets = set(self.formulas) if force else set(self.dirty)
        if not targets and not force:
            return False
        if self.cyclic and self.iterations > 0:
            # iterative calculation: reset participants so a previous ERR
            # does not poison the iteration
            for k in self.cyclic:
                cell = self.sheet.cells.get(k)
                if cell is not None:
                    cell.value = None
            for _ in range(self.iterations):
                for k in self.cyclic:
                    if k in targets or force:
                        self._eval(k)
        for k in self.order:
            if k in targets:
                self._eval(k)
        for k in self.cyclic:
            if (k in targets or force) and self.iterations == 0:
                cell = self.sheet.cells.get(k)
                if cell is not None:
                    cell.value = F.ERR
        self.dirty.clear()
        return True

    def _eval(self, key):
        comp = self.formulas.get(key)
        if comp is None:
            return
        ctx = Ctx(self, key)
        try:
            v = comp.fn(ctx)
        except F._Propagate as p:
            v = p.err
        except (ZeroDivisionError, ValueError, OverflowError, TypeError):
            v = F.ERR
        if isinstance(v, RangeValue):
            v = v.scalar(ctx)
        cell = self.sheet.cells.get(key)
        if cell is not None:
            cell.value = v


def _range_cells_iter(corner1, corner2):
    (c1, r1), (c2, r2) = corner1, corner2
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield c, r


def _range_contains(corner1, corner2, key):
    (c1, r1), (c2, r2) = corner1, corner2
    c, r = key
    return c1 <= c <= c2 and r1 <= r <= r2
