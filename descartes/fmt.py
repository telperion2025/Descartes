"""Number / date display formatting codes (see reference manual, 'Format codes').

A format code is a plain dict so it serializes to JSON directly, e.g.::

    {"type": "fixed", "decimals": 2}
    {"type": "date", "style": "dd-mmm-yy"}

Supported types: general, fixed, sci, currency, percent, comma, text, hidden,
date (styles dd-mmm-yy, dd-mmm, mmm-yy, dd/mm/yy, dd/mm/yyyy, mm/dd/yy),
time (hh:mm:ss, hh:mm).
"""

from __future__ import annotations

import datetime as _dt
import math as _math

# Lotus 1-2-3 date serials: serial 1 = 1900-01-01, with the historical
# fictitious 1900-02-29 at serial 60 (so serial 61 = 1900-03-01).
_EPOCH_LEAP = _dt.date(1899, 12, 30)   # valid for serials >= 61
_EPOCH_PRE = _dt.date(1899, 12, 31)    # valid for serials <= 60

MONTHS3 = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def lotus_to_date(serial: float) -> _dt.date:
    n = int(serial)
    if n >= 61:
        return _EPOCH_LEAP + _dt.timedelta(days=n)
    return _EPOCH_PRE + _dt.timedelta(days=n)


def lotus_to_ymd(serial: float):
    """(year, month, day) with the fictitious 1900-02-29 at serial 60."""
    n = int(serial)
    if n == 60:
        return (1900, 2, 29)
    d = lotus_to_date(serial)
    return (d.year, d.month, d.day)


def date_to_lotus(d: _dt.date) -> int:
    if d >= _dt.date(1900, 3, 1):
        return (d - _EPOCH_LEAP).days
    return (d - _EPOCH_PRE).days


def serial_to_datetime(serial: float) -> _dt.datetime:
    whole = int(serial)
    frac = serial - whole
    if whole == 0:
        base = _dt.datetime(1899, 12, 31)
    elif whole >= 61:
        base = _dt.datetime(1899, 12, 30) + _dt.timedelta(days=whole)
    else:
        base = _dt.datetime(1899, 12, 31) + _dt.timedelta(days=whole)
    return base + _dt.timedelta(seconds=round(frac * 86400))


def datetime_to_serial(dt: _dt.datetime) -> float:
    d = dt.date()
    days = date_to_lotus(d)
    secs = (dt - _dt.datetime.combine(d, _dt.time())).total_seconds()
    return days + secs / 86400.0


# ---------------------------------------------------------------------------
# Format code helpers
# ---------------------------------------------------------------------------

def make(kind: str, **kw) -> dict:
    d = {"type": kind}
    d.update(kw)
    return d


FIXED = lambda decimals=2: make("fixed", decimals=decimals)


def general_number(value: float) -> str:
    """1-2-3 'General' rendering: shortest round-trip-ish, no exponent unless needed."""
    if not _math.isfinite(value):
        return "ERR"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    s = format(value, ".12g")
    return s


def _thousands(int_part: str) -> str:
    neg = int_part.startswith("-")
    digits = int_part[1:] if neg else int_part
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return ("-" if neg else "") + ",".join(groups)


def format_number(value: float, fmt: dict | None) -> str:
    if not _math.isfinite(value):
        return "ERR"
    if fmt is None:
        return general_number(value)
    kind = fmt.get("type", "general")
    if kind == "hidden":
        return ""
    if kind == "fixed":
        return f"{value:.{fmt.get('decimals', 2)}f}"
    if kind == "comma":
        d = fmt.get("decimals", 2)
        return f"{value:,.{d}f}"
    if kind == "currency":
        d = fmt.get("decimals", 2)
        return "$" + f"{value:,.{d}f}"
    if kind == "percent":
        d = fmt.get("decimals", 0)
        return f"{value * 100:.{d}f}%"
    if kind == "sci":
        d = fmt.get("decimals", 2)
        s = f"{value:.{d}E}"
        mant, exp = s.split("E")
        sign = exp[0]
        digits = exp[1:].lstrip("0") or "0"
        return f"{mant}E{sign}{int(digits):02d}"
    if kind == "date":
        return format_date(value, fmt.get("style", "dd-mmm-yy"))
    if kind == "time":
        return format_time(value, fmt.get("style", "hh:mm:ss"))
    if kind == "text":
        return general_number(value)
    return general_number(value)


def format_date(serial: float, style: str = "dd-mmm-yy") -> str:
    try:
        year, month, day = lotus_to_ymd(serial)
    except (OverflowError, ValueError):
        return "ERR"
    yy = f"{year % 100:02d}"
    yyyy = f"{year:04d}"
    dd = f"{day:02d}"
    mmm = MONTHS3[month - 1]
    mm = f"{month:02d}"
    if style == "dd-mmm-yy":
        return f"{dd}-{mmm}-{yy}"
    if style == "dd-mmm":
        return f"{dd}-{mmm}"
    if style == "mmm-yy":
        return f"{mmm}-{yy}"
    if style == "dd/mm/yy":
        return f"{dd}/{mm}/{yy}"
    if style == "mm/dd/yy":
        return f"{mm}/{dd}/{yy}"
    if style == "dd/mm/yyyy":
        return f"{dd}/{mm}/{yyyy}"
    if style == "dd-mmm-yyyy":
        return f"{dd}-{mmm}-{yyyy}"
    return f"{dd}-{mmm}-{yy}"


def format_time(serial: float, style: str = "hh:mm:ss") -> str:
    dt = serial_to_datetime(serial)
    t = dt.time()
    if style == "hh:mm":
        return f"{t.hour:02d}:{t.minute:02d}"
    if style == "hh:mm:ss":
        return f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    return f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"


def display(value, fmt: dict | None) -> str:
    """Render a cell value for the grid. Errors render as ERR / NA."""
    from .functions import Error  # local import to avoid a cycle
    if isinstance(value, Error):
        return value.code
    if value is None:
        return ""
    if isinstance(value, str):
        if fmt is not None and fmt.get("type") == "hidden":
            return ""
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if fmt is not None and fmt.get("type") == "text":
            return general_number(float(value))
        return format_number(float(value), fmt)
    return str(value)


# ---------------------------------------------------------------------------
# WK1 format byte <-> format code
# ---------------------------------------------------------------------------

# WK1 format byte: bit7 protected, bits 4-6 type, bits 0-3 decimals/special.
_WK1_TYPES = {0: "fixed", 1: "sci", 2: "currency", 3: "percent", 4: "comma"}
_WK1_SPECIAL = {
    2: ("date", "dd/mm/yy"), 3: ("date", "dd/mm"), 4: ("date", "mm/yy"),
    5: ("text", None), 6: ("hidden", None),
    7: ("date", "dd-mmm-yyyy"), 8: ("date", "dd-mmm-yy"),
    9: ("date", "dd/mm/yy"), 10: ("date", "dd-mm-yy"),
    11: ("time", "hh:mm:ss"), 12: ("time", "hh:mm"),
}


def wk1_byte_to_fmt(byte: int):
    """Decode a WK1 cell format byte to a format code dict (or None)."""
    if byte == 0xFF:
        return None
    ftype = (byte & 0x70) >> 4
    spec = byte & 0x0F
    prot = bool(byte & 0x80)
    if ftype in _WK1_TYPES:
        fmt = make(_WK1_TYPES[ftype], decimals=spec)
    elif ftype == 7 and spec in _WK1_SPECIAL:
        kind, style = _WK1_SPECIAL[spec]
        fmt = make(kind, style=style) if style else make(kind)
    elif ftype == 5 or ftype == 6:
        fmt = make("general")
    else:
        fmt = make("fixed", decimals=spec)
    if prot:
        fmt["protected"] = True
    return fmt


def fmt_to_wk1_byte(fmt: dict | None) -> int:
    """Encode a format code dict as a WK1 format byte (0xFF = default)."""
    if fmt is None:
        return 0xFF
    kind = fmt.get("type", "general")
    dec = fmt.get("decimals", 2)
    if kind in ("fixed", "sci", "currency", "percent", "comma"):
        t = {v: k for k, v in _WK1_TYPES.items()}[kind]
        return ((t << 4) | (dec & 0x0F)) | (0x80 if fmt.get("protected") else 0)
    if kind == "date":
        style = fmt.get("style", "dd-mmm-yy")
        spec = {"dd/mm/yy": 2, "dd/mm": 3, "mm/yy": 4, "dd-mmm-yyyy": 7,
                "dd-mmm-yy": 8, "dd-mm-yy": 10}.get(style, 8)
        return (7 << 4) | spec
    if kind == "time":
        spec = 11 if fmt.get("style", "hh:mm:ss") == "hh:mm:ss" else 12
        return (7 << 4) | spec
    if kind == "text":
        return (7 << 4) | 5
    if kind == "hidden":
        return (7 << 4) | 6
    return 0xFF
