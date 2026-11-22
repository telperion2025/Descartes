"""Persistence: native .vg1 (JSON), WK1 (Lotus 1-2-3 v2 binary) import/export,
and DIF / TEXT / NUMBERS import.

.vg1 is the native format: cell raw text + formats + column widths + named
ranges + recalc mode + cursor/window state + graph definitions, with
deterministic key ordering (sheet, row, col) for clean diffs.

WK1 record stream: {u16 opcode LE, u16 length LE, payload[length]}.
Cell records: 0x000D INTEGER, 0x000E NUMBER, 0x000F LABEL, 0x0010 FORMULA
(RPN tokens + cached result). WK3 files are read as "first sheet only".
"""

from __future__ import annotations

import json
import struct

from . import fmt as _fmt
from .engine import Engine, FormulaSyntaxError, parse, serialize
from .grid import (EMPTY, FORMULA, LABEL, NUMBER, Cell, Worksheet,
                   col_index, col_letters, format_address, format_range,
                   normalize_corners, parse_address, range_cells, split_range)

VG1_FORMAT = "descartes-vg1"
VG1_VERSION = 1


# ---------------------------------------------------------------------------
# .vg1 native format
# ---------------------------------------------------------------------------

def save_vg1(path, sheets: dict[str, Worksheet], names: dict | None = None,
             cursor=(0, 0), recalc_mode="auto", iterations=0, graphs=None,
             print_settings=None, window=None):
    """Write the native .vg1 JSON file. Deterministic ordering (sheet,row,col)."""
    doc = {
        "format": VG1_FORMAT,
        "version": VG1_VERSION,
        "sheets": {},
        "names": {},
        "recalc": {"mode": recalc_mode, "iterations": iterations},
        "cursor": [cursor[0], cursor[1]],
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
        doc["names"][name] = {
            "sheet": n.get("sheet", "A"),
            "range": [list(n["range"][0]), list(n["range"][1])],
        }
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
        names[name] = {"sheet": n.get("sheet", "A"), "range": ((c1, r1), (c2, r2))}
    recalc = doc.get("recalc", {})
    c = doc.get("cursor", [0, 0])
    return {
        "sheets": sheets,
        "names": names,
        "recalc_mode": recalc.get("mode", "auto"),
        "iterations": int(recalc.get("iterations", 0)),
        "cursor": (int(c[0]), int(c[1])),
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
    """Import a WK1/WKS file (WK3 read as first sheet only) -> load_vg1-style dict."""
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
        elif cell.kind == FORMULA and engine is not None and (c, r) in engine.formulas:
            comp = engine.formulas[(c, r)]
            tokens = _infix_to_rpn(comp.ast, r, c)
            value = cell.value if isinstance(cell.value, (int, float)) else 0.0
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
                ws.set_entry(col, row, s)
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
                ws.set_entry(col, row, field[1:-1].replace('""', '"'))
            elif numbers_only:
                try:
                    ws.set_entry(col, row, _fmt.general_number(float(field.rstrip("%"))))
                except ValueError:
                    pass
            else:
                ws.set_entry(col, row, field)
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
        return read_wk1(path)
    if lower.endswith(".dif"):
        return {"sheets": {"A": read_dif(path)}, "names": {}, "recalc_mode": "auto",
                "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
    if lower.endswith((".txt", ".csv", ".prn")):
        return {"sheets": {"A": read_text_import(path)}, "names": {}, "recalc_mode": "auto",
                "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
    # sniff: WK1 starts with 00 00 (BOF opcode)
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"\x00\x00":
        return read_wk1(path)
    return {"sheets": {"A": read_text_import(path)}, "names": {}, "recalc_mode": "auto",
            "iterations": 0, "cursor": (0, 0), "graphs": [], "print": {}, "window": {}}
