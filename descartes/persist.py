"""Persistence: native .vg1 (JSON), WK1 (Lotus 1-2-3 v2 binary) import/export,
and DIF / TEXT / NUMBERS import.

.vg1 is the native format: cell raw text + formats + column widths + named
ranges + recalc mode + cursor/window state + graph definitions, with
deterministic key ordering (sheet, row, col) for clean diffs.

WK1 record stream: {u16 opcode LE, u16 length LE, payload[length]}.
Cell records: 0x000D INTEGER, 0x000E NUMBER, 0x000F LABEL, 0x0010 FORMULA
(RPN tokens + cached result). WK3 (Release 3.x) files are read with ALL
sheets: repeated BOF..EOF blocks, tab-indexed cell records (0x14..0x19),
sheet names (0x1B/14000) and range names (0x09 USERRANGE, 3-D aware).
"""

from __future__ import annotations

import json
import struct

from . import fmt as _fmt
from .engine import Engine, FormulaSyntaxError, parse, serialize
from .grid import (EMPTY, FORMULA, LABEL, NUMBER, Cell, Worksheet,
                   col_index, col_letters, format_address, format_range,
                   normalize_corners, parse_address, parse_number_literal,
                   range_cells, split_range)

VG1_FORMAT = "descartes-vg1"
VG1_VERSION = 1


# ---------------------------------------------------------------------------
# .vg1 native format
# ---------------------------------------------------------------------------

def save_vg1(path, sheets: dict[str, Worksheet], names: dict | None = None,
             cursor=(0, 0), recalc_mode="auto", iterations=0, graphs=None,
             print_settings=None, window=None, active=None):
    """Write the native .vg1 JSON file. Deterministic ordering (sheet,row,col)."""
    doc = {
        "format": VG1_FORMAT,
        "version": VG1_VERSION,
        "sheets": {},
        "names": {},
        "recalc": {"mode": recalc_mode, "iterations": iterations},
        "cursor": [cursor[0], cursor[1]],
        "active": active or (next(iter(sheets)) if sheets else "A"),
        "graphs": graphs or [],
        "print": print_settings or {},
        "window": window or {},
    }
    for name in sorted(sheets):
        ws = sheets[name]
        cells = []
        for (c, r) in sorted(ws.cells, key=lambda k: (k[1], k[0])):
            cells.append(ws.cells[(c, r)].to_dict(c, r))
        doc["sheets"][name] = {
            "cells": cells,
            "columns": {str(c): w for c, w in sorted(ws.widths.items())},
            "global_format": ws.global_fmt,
            "protection": ws.protection,
            "label_prefix": ws.label_prefix,
            "zero_suppress": ws.zero_suppress,
        }
    for name in sorted(names or {}):
        n = names[name]
        nd = {
            "sheet": n.get("sheet", "A"),
            "range": [list(n["range"][0]), list(n["range"][1])],
        }
        if n.get("sheets"):
            nd["sheets"] = list(n["sheets"])
        doc["names"][name] = nd
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=False, ensure_ascii=False)
        f.write("\n")


def load_vg1(path):
    """Load a .vg1 file -> dict with sheets (Worksheet), names, settings."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("format") != VG1_FORMAT:
        raise ValueError(f"not a descartes .vg1 file: {path}")
    sheets: dict[str, Worksheet] = {}
    for name, sdata in doc.get("sheets", {}).items():
        ws = Worksheet(name)
        for col, w in sdata.get("columns", {}).items():
            ws.widths[int(col)] = int(w)
        for cd in sdata.get("cells", []):
            cell = Cell.from_dict(cd)
            ws.cells[(int(cd["c"]), int(cd["r"]))] = cell
        ws.global_fmt = sdata.get("global_format")
        ws.protection = bool(sdata.get("protection", False))
        ws.label_prefix = sdata.get("label_prefix", "'")
        ws.zero_suppress = bool(sdata.get("zero_suppress", False))
        sheets[name] = ws
    names = {}
    for name, n in doc.get("names", {}).items():
        (c1, r1), (c2, r2) = n["range"]
        entry = {"sheet": n.get("sheet", "A"), "range": ((c1, r1), (c2, r2))}
        if n.get("sheets"):
            entry["sheets"] = list(n["sheets"])
        names[name] = entry
    recalc = doc.get("recalc", {})
    c = doc.get("cursor", [0, 0])
    return {
        "sheets": sheets,
        "names": names,
        "recalc_mode": recalc.get("mode", "auto"),
        "iterations": int(recalc.get("iterations", 0)),
        "cursor": (int(c[0]), int(c[1])),
        "active": doc.get("active"),
        "graphs": doc.get("graphs", []),
        "print": doc.get("print", {}),
        "window": doc.get("window", {}),
    }


def attach_engine(sheet: Worksheet) -> Engine:
    """Create an Engine for a sheet, compile all formulas, force a recalc."""
    eng = Engine(sheet)
    eng.register_all()
    sheet.recalc_mode = sheet.recalc_mode or "auto"
    eng.recalc(force=True)
    return eng


# ---------------------------------------------------------------------------
# WK1 (Lotus 1-2-3 Release 2.x binary) import / export
# ---------------------------------------------------------------------------

WK1_BOF = 0x0000
WK1_EOF = 0x0001
WK1_CALCMODE = 0x0002
WK1_INTEGER = 0x000D
WK1_NUMBER = 0x000E
WK1_LABEL = 0x000F
WK1_FORMULA = 0x0010
WK1_COLW1 = 0x0008

# RPN token -> (infix template). None means unsupported for text translation.
_WK1_OPS = {
    0x08: ("un", "-"), 0x09: ("bin", "+"), 0x0A: ("bin", "-"), 0x0B: ("bin", "*"),
    0x0C: ("bin", "/"), 0x0D: ("bin", "^"), 0x0E: ("bin", "="), 0x0F: ("bin", "<>"),
    0x10: ("bin", "<="), 0x11: ("bin", ">="), 0x12: ("bin", "<"), 0x13: ("bin", ">"),
    0x14: ("bin", "#AND#"), 0x15: ("bin", "#OR#"), 0x16: ("un", "#NOT#"),
    0x17: ("bin", "&"),
}
_WK1_FUNCS = {
    0x1F: ("NA", 0), 0x20: ("ERR", 0),
    0x21: ("ABS", 1), 0x22: ("INT", 1), 0x23: ("SQRT", 1), 0x24: ("LOG", 1),
    0x25: ("LN", 1), 0x26: ("PI", 0), 0x27: ("SIN", 1), 0x28: ("COS", 1),
    0x29: ("TAN", 1), 0x2A: ("ATAN2", 2), 0x2B: ("ATAN", 1), 0x2C: ("ASIN", 1),
    0x2D: ("ACOS", 1), 0x2E: ("EXP", 1), 0x2F: ("MOD", 2), 0x30: ("CHOOSE", -1),
    0x31: ("ISNA", 1), 0x32: ("ISERR", 1), 0x35: ("RAND", 0), 0x36: ("DATE", 3),
    0x37: ("TODAY", 0), 0x38: ("PAYMENT", -1), 0x39: ("PV", -1), 0x3A: ("FV", -1),
    0x3B: ("IF", 3), 0x3C: ("DAY", 1), 0x3D: ("MONTH", 1), 0x3E: ("YEAR", 1),
    0x3F: ("ROUND", 2), 0x40: ("TIME", 3), 0x41: ("HOUR", 1), 0x42: ("MINUTE", 1),
    0x43: ("SECOND", 1), 0x44: ("ISNUMBER", 1), 0x45: ("ISSTRING", 1),
    0x46: ("LENGTH", 1), 0x47: ("VALUE", 1), 0x49: ("MID", 3), 0x4A: ("CHAR", 1),
    0x4B: ("CODE", 1), 0x4D: ("DATEVALUE", 1),
    0x50: ("SUM", -1), 0x51: ("AVG", -1), 0x52: ("COUNT", -1), 0x53: ("MIN", -1),
    0x54: ("MAX", -1), 0x55: ("VLOOKUP", 3), 0x56: ("NPV", -1), 0x57: ("VAR", 1),
    0x58: ("STD", 1), 0x59: ("IRR", -1), 0x5A: ("HLOOKUP", 3), 0x62: ("INDEX", -1),
    0x63: ("COLS", 1), 0x64: ("ROWS", 1), 0x65: ("REPEAT", 2), 0x66: ("UPPER", 1),
    0x67: ("LOWER", 1), 0x68: ("LEFT", -1), 0x69: ("RIGHT", -1), 0x6B: ("TRIM", 1),
    0x74: ("RATE", -1), 0x75: ("TERM", -1),
}


def _wk1_records(data: bytes):
    i = 0
    n = len(data)
    while i + 4 <= n:
        opcode, length = struct.unpack_from("<HH", data, i)
        payload = data[i + 4:i + 4 + length]
        if len(payload) < length:
            break
        yield opcode, payload
        i += 4 + length


def _makecoord(orig: int, coord: int) -> int:
    """Decode a WK1 formula coordinate: with the 0x8000 flag set the stored
    value is a 16-bit two's-complement offset from the formula cell."""
    if coord & 0x8000:
        coord = coord - 0x10000 + orig
    return coord & 0x3FFF


def _rpn_to_infix(tokens: bytes, row: int, col: int):
    """Translate a WK1 RPN token stream to 1-2-3 infix text (best effort).

    Returns (text, ok). On any unsupported token, ok=False and the caller
    falls back to the cached numeric result.
    """
    stack: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        op = tokens[i]
        if op == 0x00:
            if i + 9 > n:
                return "", False
            (v,) = struct.unpack_from("<d", tokens, i + 1)
            stack.append(_fmt.general_number(v))
            i += 9
        elif op == 0x01:
            if i + 5 > n:
                return "", False
            c, r = struct.unpack_from("<HH", tokens, i + 1)
            c = _makecoord(col, c)
            r = _makecoord(row, r)
            stack.append(format_address(c, r))
            i += 5
        elif op == 0x02:
            if i + 9 > n:
                return "", False
            c1, r1, c2, r2 = struct.unpack_from("<HHHH", tokens, i + 1)
            a = format_address(_makecoord(col, c1), _makecoord(row, r1))
            b = format_address(_makecoord(col, c2), _makecoord(row, r2))
            stack.append(f"{a}..{b}")
            i += 9
        elif op == 0x03:
            break
        elif op == 0x04:
            i += 1
        elif op == 0x05:
            if i + 3 > n:
                return "", False
            (v,) = struct.unpack_from("<h", tokens, i + 1)
            stack.append(str(v))
            i += 3
        elif op == 0x06:
            j = tokens.index(0, i + 1)
            s = tokens[i + 1:j].decode("cp437", "replace")
            stack.append('"' + s.replace('"', '""') + '"')
            i = j + 1
        elif op in _WK1_OPS:
            kind, sym = _WK1_OPS[op]
            if kind == "bin":
                if len(stack) < 2:
                    return "", False
                b = stack.pop()
                a = stack.pop()
                stack.append(f"({a}{sym}{b})")
            else:
                if not stack:
                    return "", False
                a = stack.pop()
                stack.append(f"({sym}{a})" if sym == "-" else f"(#NOT#{a})")
            i += 1
        elif op in _WK1_FUNCS:
            name, nargs = _WK1_FUNCS[op]
            if nargs >= 0:
                if len(stack) < nargs:
                    return "", False
                args = [stack.pop() for _ in range(nargs)][::-1]
            else:
                if i + 1 >= n:
                    return "", False
                count = tokens[i + 1]
                if len(stack) < count:
                    return "", False
                args = [stack.pop() for _ in range(count)][::-1]
                i += 1
            stack.append("@" + name + "(" + ",".join(args) + ")")
            i += 1
        else:
            return "", False
    if len(stack) != 1:
        return "", False
    return stack[0], True


def read_wk1(path) -> dict:
    """Import a WK1/WKS file -> load_vg1-style dict (single sheet A)."""
    with open(path, "rb") as f:
        data = f.read()
    ws = Worksheet("A")
    names: dict = {}
    recalc_mode = "auto"
    for opcode, payload in _wk1_records(data):
        if opcode == WK1_EOF:
            break
        if opcode == WK1_BOF:
            continue
        if opcode == WK1_CALCMODE and payload:
            recalc_mode = "manual" if payload[0] else "auto"
        elif opcode == WK1_COLW1 and len(payload) >= 3:
            col = struct.unpack_from("<H", payload, 0)[0]
            ws.widths[col] = payload[2]
        elif opcode == WK1_INTEGER and len(payload) >= 7:
            fmt = _fmt.wk1_byte_to_fmt(payload[0])
            col, row = struct.unpack_from("<HH", payload, 1)
            (v,) = struct.unpack_from("<h", payload, 5)
            cell = Cell(raw=str(v), kind=NUMBER, value=float(v), fmt=fmt)
            ws.cells[(col, row)] = cell
        elif opcode == WK1_NUMBER and len(payload) >= 13:
            fmt = _fmt.wk1_byte_to_fmt(payload[0])
            col, row = struct.unpack_from("<HH", payload, 1)
            (v,) = struct.unpack_from("<d", payload, 5)
            cell = Cell(raw=_fmt.general_number(v), kind=NUMBER, value=v, fmt=fmt)
            ws.cells[(col, row)] = cell
        elif opcode == WK1_LABEL and len(payload) >= 6:
            fmt = _fmt.wk1_byte_to_fmt(payload[0])
            col, row = struct.unpack_from("<HH", payload, 1)
            prefix = chr(payload[5]) if payload[5:6] else "'"
            text = payload[6:].split(b"\0")[0].decode("cp437", "replace")
            align = {"'": "left", "^": "center", '"': "right"}.get(prefix)
            cell = Cell(raw=prefix + text, kind=LABEL, value=text, fmt=fmt, align=align)
            ws.cells[(col, row)] = cell
        elif opcode == WK1_FORMULA and len(payload) >= 15:
            fmt = _fmt.wk1_byte_to_fmt(payload[0])
            col, row = struct.unpack_from("<HH", payload, 1)
            (cached,) = struct.unpack_from("<d", payload, 5)
            (flen,) = struct.unpack_from("<H", payload, 13)
            tokens = payload[15:15 + flen]
            text, ok = _rpn_to_infix(tokens, row, col)
            if ok:
                cell = Cell(raw="+" + text, kind=FORMULA, value=None, fmt=fmt)
            else:
                cell = Cell(raw=_fmt.general_number(cached), kind=NUMBER,
                            value=cached, fmt=fmt)
            ws.cells[(col, row)] = cell
        # all other records are skipped (window/print/graph settings)
    return {
        "sheets": {"A": ws},
        "names": names,
        "recalc_mode": recalc_mode,
        "iterations": 0,
        "cursor": (0, 0),
        "graphs": [],
        "print": {},
        "window": {},
    }


def _snum16(h: int) -> float:
    """Lotus 16-bit compressed float (WK3 SMALLNUMCELL / RPN snum token)."""
    if not (h & 1):
        return h / 2
    mult = {0x1: 5000.0, 0x3: 500.0, 0x5: 0.05, 0x7: 0.005,
            0x9: 0.0005, 0xB: 5e-5, 0xD: 1.0 / 16, 0xF: 1.0 / 64}
    m = h & 0xF
    if m not in mult:
        return 0.0
    return (h >> 4) * mult[m]


def _x87_to_double(b: bytes) -> float:
    """10-byte x87 extended float (WK3 RPN Const10Float token)."""
    mant = int.from_bytes(b[0:8], "little")
    se = int.from_bytes(b[8:10], "little")
    sign = -1.0 if (se & 0x8000) else 1.0
    exp = se & 0x7FFF
    if exp == 0x7FFF:
        return sign * float("inf")
    if exp == 0:
        return sign * mant * 2.0 ** (-16382 - 63)
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def _rpn3_to_infix(tokens: bytes, row: int, col: int, tab: int, order: list):
    """Translate a WK3 RPN token stream to 1-2-3 infix text (best effort).

    WK3 opcodes 0x0F..0x1E are the WK1 operators shifted by +7; 0x1F.. are the
    same function opcodes as WK1; 0x00 is a 10-byte x87 float, 0x01 a 16-bit
    compressed number, 0x02/0x03 cell/range refs with a tab byte.
    Returns (text, ok); on any unsupported token ok=False (caller uses the
    cached result)."""
    stack: list[str] = []
    i = 0
    n = len(tokens)

    def sheet_name(t):
        if 0 <= t < len(order):
            return order[t]
        return None

    def addr(c, r, t):
        sn = sheet_name(t)
        a = format_address(c, r)
        if sn is not None and t != tab:
            return sn + "!" + a
        return a

    while i < n:
        op = tokens[i]
        if op == 0x00:
            if i + 11 > n:
                return "", False
            stack.append(_fmt.general_number(_x87_to_double(tokens[i + 1:i + 11])))
            i += 11
        elif op == 0x01:
            if i + 3 > n:
                return "", False
            (v,) = struct.unpack_from("<h", tokens, i + 1)
            stack.append(_fmt.general_number(_snum16(v)))
            i += 3
        elif op == 0x02:
            if i + 6 > n:
                return "", False
            rel, r, t, c = struct.unpack_from("<BHBB", tokens, i + 1)
            stack.append(addr(c, r, t))
            i += 6
        elif op == 0x03:
            if i + 10 > n:
                return "", False
            rel, r1, t1, c1, r2, t2, c2 = struct.unpack_from("<BHBBHBB", tokens, i + 1)
            s1, s2 = sheet_name(t1), sheet_name(t2)
            a = format_address(c1, r1)
            b = format_address(c2, r2)
            if s1 and s2 and t1 != t2:
                stack.append(f"{s1}:{s2}!{a}..{b}")
            elif s1 and t1 != tab:
                stack.append(f"{s1}!{a}..{b}")
            else:
                stack.append(f"{a}..{b}")
            i += 10
        elif op in (0x04, 0x05):
            j = tokens.find(b"\0", i + 1)
            if j < 0:
                return "", False
            nm = tokens[i + 1:j].decode("cp437", "replace")
            stack.append(nm.upper())
            i = j + 1
        elif op in (0x06, 0x07, 0x08):
            stack.append("@ERR()")
            i += {0x06: 5, 0x07: 6, 0x08: 11}[op]
        elif op == 0x09:
            return "", False
        elif op == 0x0A:
            break
        elif op == 0x0B:
            i += 1
        elif op == 0x0C:
            if i + 3 > n:
                return "", False
            (v,) = struct.unpack_from("<h", tokens, i + 1)
            stack.append(str(v))
            i += 3
        elif op == 0x0D:
            j = tokens.find(b"\0", i + 1)
            if j < 0:
                return "", False
            s = tokens[i + 1:j].decode("cp437", "replace")
            stack.append('"' + s.replace('"', '""') + '"')
            i = j + 1
        elif op == 0x0E:
            i += 1
        elif 0x0F <= op <= 0x1E:
            wop = op - 7
            if wop in _WK1_OPS:
                kind, sym = _WK1_OPS[wop]
                if kind == "bin":
                    if len(stack) < 2:
                        return "", False
                    b = stack.pop()
                    a = stack.pop()
                    stack.append(f"({a}{sym}{b})")
                else:
                    if not stack:
                        return "", False
                    a = stack.pop()
                    stack.append(f"({sym}{a})" if sym == "-" else f"(#NOT#{a})")
                i += 1
            else:
                return "", False
        elif op in _WK1_FUNCS:
            name, nargs = _WK1_FUNCS[op]
            if nargs >= 0:
                if len(stack) < nargs:
                    return "", False
                args = [stack.pop() for _ in range(nargs)][::-1]
            else:
                if i + 1 >= n:
                    return "", False
                count = tokens[i + 1]
                if len(stack) < count:
                    return "", False
                args = [stack.pop() for _ in range(count)][::-1]
                i += 1
            stack.append("@" + name + "(" + ",".join(args) + ")")
            i += 1
        else:
            return "", False
    if len(stack) != 1:
        return "", False
    return stack[0], True


def read_wk3(path) -> dict:
    """Import a WK3 (Lotus 1-2-3 Release 3.x) file: ALL sheets.

    Structure (per the LibreOffice lotus filter): a sequence of sheet blocks,
    each BOF (opcode 0x0000, version >= 0x1000, subtype 0x0004) ... EOF
    (0x0001). Cell records carry a tab index: 0x14 ERR, 0x15 NA, 0x16 LABEL
    (addr6 + prefix + cstring), 0x17 NUMBER (addr6 + double), 0x18 SMALLNUM
    (addr6 + snum16), 0x19 FORMULA (addr6 + double + u16 len + WK3 RPN).
    0x07 COLUMNWIDTH, 0x09 USERRANGE (names), 0x1B subtype 14000 = sheet name.
    """
    with open(path, "rb") as f:
        data = f.read()
    sheets: dict[str, Worksheet] = {}
    order: list[str] = []
    names: dict = {}
    recalc_mode = "auto"
    cur = None

    def ws_for_tab(t):
        if 0 <= t < len(order):
            return sheets[order[t]]
        return cur

    for opcode, payload in _wk1_records(data):
        if opcode == 0x0000:
            if len(payload) >= 4:
                version, sub = struct.unpack_from("<HH", payload, 0)
                if version >= 0x1000 and sub == 0x0004:
                    name = col_letters(len(order))
                    cur = Worksheet(name)
                    sheets[name] = cur
                    order.append(name)
                elif not order:
                    cur = Worksheet("A")
                    sheets["A"] = cur
                    order.append("A")
            continue
        if opcode == 0x0001:
            cur = None
            continue
        if opcode == 0x0002:
            raise ValueError("file is password protected")
        if cur is None:
            continue
        if opcode == 0x0007 and len(payload) >= 4:
            if not payload[1]:
                off = 4
                while off + 2 <= len(payload):
                    col = payload[off]
                    w = payload[off + 1]
                    if w:
                        cur.widths[col] = max(1, w)
                    off += 2
        elif opcode == 0x0009 and len(payload) >= 26:
            raw_name = payload[2:18].split(b"\0")[0]
            name = raw_name.decode("cp437", "replace").upper()
            c1, r1, c2, r2 = struct.unpack_from("<HHHH", payload, 18)
            entry = {"sheet": cur.name if cur is not None else order[0],
                     "range": ((c1, r1), (c2, r2))}
            if len(payload) >= 30:
                t1, t2 = struct.unpack_from("<HH", payload, 26)
                if 0 <= t1 < len(order) and 0 <= t2 < len(order):
                    a, b = order[t1], order[t2]
                    entry["sheet"] = a
                    if a != b:
                        entry["sheets"] = [a, b]
            if name:
                names[name] = entry
        elif opcode in (0x0014, 0x0015) and len(payload) >= 6:
            c, r, t = struct.unpack_from("<HHH", payload, 0)
            ws = ws_for_tab(t) or cur
            raw = "+@ERR()" if opcode == 0x0014 else "+@NA()"
            ws.cells[(c, r)] = Cell(raw=raw, kind=FORMULA, value=None)
        elif opcode == 0x0016 and len(payload) >= 7:
            c, r, t = struct.unpack_from("<HHH", payload, 0)
            prefix = chr(payload[6]) if payload[6:7] else "'"
            text = payload[7:].split(b"\0")[0].decode("cp437", "replace")
            ws = ws_for_tab(t) or cur
            align = {"'": "left", "^": "center", '"': "right"}.get(prefix)
            ws.cells[(c, r)] = Cell(raw=prefix + text, kind=LABEL, value=text,
                                    align=align)
        elif opcode == 0x0017 and len(payload) >= 14:
            c, r, t = struct.unpack_from("<HHH", payload, 0)
            (v,) = struct.unpack_from("<d", payload, 6)
            ws = ws_for_tab(t) or cur
            ws.cells[(c, r)] = Cell(raw=_fmt.general_number(v), kind=NUMBER,
                                    value=float(v))
        elif opcode == 0x0018 and len(payload) >= 8:
            c, r, t = struct.unpack_from("<HHH", payload, 0)
            (v,) = struct.unpack_from("<h", payload, 6)
            val = _snum16(v)
            ws = ws_for_tab(t) or cur
            ws.cells[(c, r)] = Cell(raw=_fmt.general_number(val), kind=NUMBER,
                                    value=val)
        elif opcode == 0x0019 and len(payload) >= 16:
            c, r, t = struct.unpack_from("<HHH", payload, 0)
            (cached,) = struct.unpack_from("<d", payload, 6)
            (flen,) = struct.unpack_from("<H", payload, 14)
            tokens = payload[16:16 + flen]
            ws = ws_for_tab(t) or cur
            text, ok = _rpn3_to_infix(tokens, r, c, t, order)
            if ok:
                ws.cells[(c, r)] = Cell(raw="+" + text, kind=FORMULA, value=None)
            else:
                ws.cells[(c, r)] = Cell(raw=_fmt.general_number(cached),
                                        kind=NUMBER, value=cached)
        elif opcode == 0x001B and len(payload) > 4:
            sub = struct.unpack_from("<H", payload, 0)[0]
            if sub == 14000:
                tab = struct.unpack_from("<H", payload, 2)[0]
                nm = payload[4:].split(b"\0")[0].decode("cp437", "replace").upper()
                if 0 <= tab < len(order) and nm and nm not in sheets:
                    old = order[tab]
                    ws = sheets.pop(old)
                    ws.name = nm
                    sheets[nm] = ws
                    order[tab] = nm
                    for e in names.values():
                        if e.get("sheet") == old:
                            e["sheet"] = nm
                        if "sheets" in e and old in e["sheets"]:
                            e["sheets"] = [nm if s == old else s for s in e["sheets"]]
    return {
        "sheets": sheets,
        "names": names,
        "recalc_mode": recalc_mode,
        "iterations": 0,
        "cursor": (0, 0),
        "active": order[0] if order else None,
        "graphs": [],
        "print": {},
        "window": {},
    }


def read_lotus(path) -> dict:
    """WK1/WKS or WK3, chosen by the first BOF version word."""
    with open(path, "rb") as f:
        head = f.read(6)
    if len(head) >= 6:
        opcode, length = struct.unpack_from("<HH", head, 0)
        if opcode == 0x0000 and length >= 4:
            (version,) = struct.unpack_from("<H", head, 4)
            if version >= 0x1000:
                return read_wk3(path)
    return read_wk1(path)


def _infix_to_rpn(ast, row: int, col: int) -> bytes | None:
    """Encode an AST as WK1 RPN tokens (common subset). None if unsupported."""
    out = bytearray()

    def enc_ref(c, r, ac, ar):
        if ac:
            return struct.pack("<HH", c, r)
        return struct.pack("<HH", ((c - col) & 0x3FFF) | 0x8000,
                           ((r - row) & 0x3FFF) | 0x8000)

    def enc(node) -> bool:
        kind = node[0]
        if kind == "num":
            out.append(0x00)
            out.extend(struct.pack("<d", node[1]))
            return True
        if kind == "str":
            out.append(0x06)
            out.extend(node[1].encode("cp437", "replace") + b"\0")
            return True
        if kind == "cell":
            out.append(0x01)
            out.extend(enc_ref(node[1], node[2], node[3], node[4]))
            return True
        if kind == "range":
            out.append(0x02)
            (c1, r1, a1, b1), (c2, r2, a2, b2) = node[1], node[2]
            out.extend(enc_ref(c1, r1, a1, b1))
            out.extend(enc_ref(c2, r2, a2, b2))
            return True
        if kind == "bin":
            if not enc(node[2]) or not enc(node[3]):
                return False
            rev = {v: k for k, v in _WK1_OPS.items()}
            op = rev.get(("bin", node[1]))
            if op is None:
                return False
            out.append(op)
            return True
        if kind == "un":
            if not enc(node[2]):
                return False
            rev = {v: k for k, v in _WK1_OPS.items()}
            op = rev.get(("un", node[1]))
            if op is None:
                return False
            out.append(op)
            return True
        if kind == "func":
            name = node[1]
            rev = {v[0]: (k, v[1]) for k, v in _WK1_FUNCS.items()}
            if name not in rev:
                return False
            opcode, nargs = rev[name]
            for a in node[2]:
                if not enc(a):
                    return False
            out.append(opcode)
            if nargs < 0:
                out.append(len(node[2]) & 0xFF)
            return True
        return False

    if not enc(ast):
        return None
    out.append(0x03)
    return bytes(out)


def write_wk1(path, sheet: Worksheet, engine: Engine | None = None):
    """Export a worksheet as a WK1 (Release 2.x) binary file."""
    out = bytearray()
    out += struct.pack("<HHH", WK1_BOF, 2, 0x0406)   # BOF, len 2, version 0x0406
    out += struct.pack("<HHB", WK1_CALCMODE, 1, 0 if sheet.recalc_mode == "auto" else 1)
    for col in sorted(sheet.widths):
        out += struct.pack("<HHHB", WK1_COLW1, 3, col, sheet.widths[col])
    for (c, r) in sorted(sheet.cells, key=lambda k: (k[1], k[0])):
        cell = sheet.cells[(c, r)]
        fb = _fmt.fmt_to_wk1_byte(cell.fmt)
        if cell.kind == LABEL:
            text = (cell.value or "")
            prefix = cell.raw[0] if cell.raw and cell.raw[0] in "'^\"" else "'"
            payload = struct.pack("<BHH", fb, c, r) + prefix.encode("cp437") + \
                text.encode("cp437", "replace") + b"\0"
            out += struct.pack("<HH", WK1_LABEL, len(payload)) + payload
        elif cell.kind == FORMULA:
            comp = engine.formulas.get((c, r)) if engine is not None else None
            tokens = _infix_to_rpn(comp.ast, r, c) if comp is not None else None
            if tokens is None and comp is None:
                try:
                    parse(cell.raw)
                except FormulaSyntaxError:
                    try:
                        tokens = _infix_to_rpn(parse("+@ERR()"), r, c)
                    except FormulaSyntaxError:
                        tokens = None
            value = cell.value if isinstance(cell.value, (int, float)) \
                and not isinstance(cell.value, bool) else 0.0
            if tokens is None:
                payload = struct.pack("<BHH", fb, c, r) + struct.pack("<d", float(value))
                out += struct.pack("<HH", WK1_NUMBER, len(payload)) + payload
            else:
                payload = struct.pack("<BHH", fb, c, r) + struct.pack("<dH", float(value), len(tokens)) + tokens
                out += struct.pack("<HH", WK1_FORMULA, len(payload)) + payload
        elif isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
            v = float(cell.value)
            if v == int(v) and abs(v) < 32768:
                payload = struct.pack("<BHHh", fb, c, r, int(v))
                out += struct.pack("<HH", WK1_INTEGER, len(payload)) + payload
            else:
                payload = struct.pack("<BHHd", fb, c, r, v)
                out += struct.pack("<HH", WK1_NUMBER, len(payload)) + payload
    out += struct.pack("<HH", WK1_EOF, 0)
    with open(path, "wb") as f:
        f.write(bytes(out))


# ---------------------------------------------------------------------------
# DIF (Data Interchange Format) import / export
# ---------------------------------------------------------------------------

def read_dif(path) -> Worksheet:
    """Import a DIF file (numbers and string labels only; no formulas)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\r\n") for ln in f]
    ws = Worksheet("A")
    i = 0
    n = len(lines)
    row = col = 0
    while i < n:
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        parts = ln.split(",")
        tag = parts[0].strip().upper()
        if tag == "TABLE":
            i += 2  # skip TABLE / 0,1 vector header
            continue
        if tag == "TUPLES":
            i += 2
            continue
        if tag == "VECTORS":
            i += 2
            continue
        if tag == "DATA":
            i += 2
            continue
        if tag == "V":
            if i + 1 < n:
                try:
                    v = float(lines[i + 1].strip())
                    ws.set_entry(col, row, _fmt.general_number(v))
                except ValueError:
                    pass
                i += 2
                col += 1
                continue
        if tag == "S":
            if i + 1 < n:
                s = lines[i + 1].strip()
                if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
                    s = s[1:-1]
                ws.set_entry(col, row, "'" + s)
                i += 2
                col += 1
                continue
        if tag == "-1" or tag == "-1,0" or ln == "-1":
            col = 0
            row += 1
            i += 1
            continue
        if tag == "EOD":
            break
        i += 1
    return ws


def write_dif(path, sheet: Worksheet, corner1=(0, 0), corner2=None):
    """Export a range as DIF."""
    if corner2 is None:
        corner2 = sheet.used_bounds()[1]
    (c1, r1), (c2, r2) = normalize_corners(corner1, corner2)
    lines = ["TABLE", "0,1", '"descartes"', "VECTORS", f"0,{c2 - c1 + 1}", '"COL"',
             "TUPLES", f"0,{r2 - r1 + 1}", "DATA", "0,0", '""']
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cell = sheet.cells.get((c, r))
            if cell is None or cell.kind == EMPTY or cell.kind == FORMULA:
                v = cell.value if cell is not None else None
                if isinstance(v, (int, float)):
                    lines.append("V")
                    lines.append(_fmt.general_number(float(v)))
                else:
                    lines.append("V")
                    lines.append("0")
            elif isinstance(cell.value, str):
                lines.append("S")
                lines.append('"' + cell.value.replace('"', '""') + '"')
            else:
                lines.append("V")
                lines.append(_fmt.general_number(float(cell.value)))
        lines.append("-1,0")
    lines.append("EOD")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")


# ---------------------------------------------------------------------------
# TEXT / NUMBERS import (1-2-3 style delimited text)
# ---------------------------------------------------------------------------

def read_text_import(path, numbers_only=False) -> Worksheet:
    """Import a 1-2-3 'Text' file: quoted strings, numbers, comma/newline
    delimited. If numbers_only, non-numeric fields become blank."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    ws = Worksheet("A")
    row = 0
    for line in content.splitlines():
        if not line.strip():
            row += 1
            continue
        col = 0
        for field in _split_text_line(line):
            field = field.strip()
            if not field:
                col += 1
                continue
            if len(field) >= 2 and field.startswith('"') and field.endswith('"'):
                ws.set_entry(col, row, "'" + field[1:-1].replace('""', '"'))
            elif numbers_only:
                try:
                    ws.set_entry(col, row, _fmt.general_number(float(field.rstrip("%"))))
                except ValueError:
                    pass
            elif parse_number_literal(field) is not None:
                ws.set_entry(col, row, field)
            else:
                ws.set_entry(col, row, "'" + field)
            col += 1
        row += 1
    return ws


def _split_text_line(line: str) -> list[str]:
    fields = []
    cur = []
    in_q = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_q:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur.append('""')
                    i += 2
                    continue
                cur.append('"')
                in_q = False
                i += 1
                continue
            cur.append(ch)
            i += 1
        else:
            if ch == '"':
                in_q = True
                cur.append(ch)
                i += 1
            elif ch == ",":
                fields.append("".join(cur))
                cur = []
                i += 1
            else:
                cur.append(ch)
                i += 1
    fields.append("".join(cur))
    return fields


def import_file(path) -> dict:
    """Import any supported file by extension/content -> load_vg1-style dict."""
    lower = path.lower()
    if lower.endswith(".vg1"):
        return load_vg1(path)
    if lower.endswith((".wk1", ".wks", ".123", ".wk3")):
        return read_lotus(path)
    if lower.endswith(".dif"):
        return {"sheets": {"A": read_dif(path)}, "names": {}, "recalc_mode": "auto",
                "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
    if lower.endswith((".txt", ".csv", ".prn")):
        return {"sheets": {"A": read_text_import(path)}, "names": {}, "recalc_mode": "auto",
                "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
    # sniff: Lotus files start with 00 00 (BOF opcode)
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"\x00\x00":
        return read_lotus(path)
    return {"sheets": {"A": read_text_import(path)}, "names": {}, "recalc_mode": "auto",
            "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
