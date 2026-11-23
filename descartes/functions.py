"""@function registry for the formula engine.

Every function receives ``(args, ctx)`` where ``args`` is a list of evaluated
arguments (scalars, or range objects exposing ``.values()`` / ``.cells()``) and
``ctx`` provides ``ctx.val(col, row)`` plus the formula cell key ``ctx.fkey``.
"""

from __future__ import annotations

import datetime as _dt
import math
import random

from . import fmt as _fmt


# ---------------------------------------------------------------------------
# Value model helpers
# ---------------------------------------------------------------------------

class Error:
    """A 1-2-3 error value: ERR or NA."""

    __slots__ = ("code",)
    _is_lotus_error = True   # duck-typing marker (survives module re-import)

    def __init__(self, code: str = "ERR"):
        self.code = code

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, Error) and other.code == self.code

    def __hash__(self):
        return hash(("Error", self.code))


ERR = Error("ERR")
NA = Error("NA")


def iserr(v) -> bool:
    return isinstance(v, Error) and v.code == "ERR"


def isna(v) -> bool:
    return isinstance(v, Error) and v.code == "NA"


def iserror(v) -> bool:
    return isinstance(v, Error)


def propagate(args):
    """Return the first error found in args, else None (1-2-3 propagation)."""
    for a in args:
        if isinstance(a, Error):
            return a
        values = getattr(a, "values", None)
        if callable(values):
            for v in values():
                if isinstance(v, Error):
                    return v
    return None


def to_num(v):
    """Arithmetic coercion: blank -> 0, number -> float, text -> ERR."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Error):
        return v
    return ERR


def to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Error):
        return v.code
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return _fmt.general_number(v)
    return str(v)


def flatten(args):
    """Flatten args to scalars; range args expand via .values(), blanks -> None."""
    out = []
    for a in args:
        values = getattr(a, "values", None)
        if callable(values):
            out.extend(values())
        else:
            out.append(a)
    return out


def numeric_values(args):
    """Numbers for statistical functions: from ranges, only real numbers count;
    direct scalar arguments are coerced strictly (text -> ERR)."""
    nums = []
    for a in args:
        values = getattr(a, "values", None)
        if callable(values):
            for v in values():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    nums.append(float(v))
                # labels / blanks / errors inside ranges are ignored by 1-2-3
        else:
            if isinstance(a, Error):
                return a
            if a is None:
                continue
            if isinstance(a, bool):
                nums.append(1.0 if a else 0.0)
            elif isinstance(a, (int, float)):
                nums.append(float(a))
            else:
                try:
                    nums.append(float(str(a).strip().rstrip("%")))
                except ValueError:
                    return ERR
    return nums


def lotus_compare(a, b):
    """1-2-3 comparison order: numbers < text; text compared case-insensitively.

    Returns -1 / 0 / 1. Blank compares as 0 against numbers, "" against text.
    """
    if isinstance(a, Error):
        raise _Propagate(a)
    if isinstance(b, Error):
        raise _Propagate(b)
    if a is None and b is None:
        return 0
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a is None:
        a_num, a = not b_num, (0.0 if b_num else "")
    if b is None:
        b_num, b = not a_num, (0.0 if a_num else "")
    if a_num and b_num:
        return -1 if a < b else (0 if a == b else 1)
    if a_num != b_num:
        return -1 if a_num else 1
    sa, sb = str(a).upper(), str(b).upper()
    return -1 if sa < sb else (0 if sa == sb else 1)


class _Propagate(Exception):
    def __init__(self, err):
        self.err = err


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FUNCS: dict[str, callable] = {}


def func(name: str):
    def deco(fn):
        FUNCS[name.upper()] = fn
        return fn
    return deco


def call(name: str, args, ctx):
    fn = FUNCS.get(name.upper())
    if fn is None:
        return ERR
    try:
        return fn(args, ctx)
    except _Propagate as p:
        return p.err
    except (ZeroDivisionError, ValueError, OverflowError, IndexError, TypeError):
        return ERR


# ---------------------------------------------------------------------------
# Numeric / statistical
# ---------------------------------------------------------------------------

@func("SUM")
def _sum(args, ctx):
    e = propagate(args)
    if e:
        return e
    nums = numeric_values(args)
    if isinstance(nums, Error):
        return nums
    return math.fsum(nums)


@func("AVG")
def _avg(args, ctx):
    e = propagate(args)
    if e:
        return e
    nums = numeric_values(args)
    if isinstance(nums, Error):
        return nums
    if not nums:
        return ERR
    return math.fsum(nums) / len(nums)


@func("COUNT")
def _count(args, ctx):
    e = propagate(args)
    if e:
        return e
    n = 0
    for a in args:
        values = getattr(a, "values", None)
        if callable(values):
            n += sum(1 for v in values() if isinstance(v, (int, float)) and not isinstance(v, bool))
        elif isinstance(a, (int, float)) and not isinstance(a, bool):
            n += 1
    return float(n)


@func("MAX")
def _max(args, ctx):
    e = propagate(args)
    if e:
        return e
    nums = numeric_values(args)
    if isinstance(nums, Error):
        return nums
    return max(nums) if nums else 0.0


@func("MIN")
def _min(args, ctx):
    e = propagate(args)
    if e:
        return e
    nums = numeric_values(args)
    if isinstance(nums, Error):
        return nums
    return min(nums) if nums else 0.0


@func("VAR")
def _var(args, ctx):
    nums = numeric_values(args)
    if isinstance(nums, Error):
        return nums
    if len(nums) < 2:
        return ERR
    m = math.fsum(nums) / len(nums)
    return math.fsum((x - m) ** 2 for x in nums) / (len(nums) - 1)


@func("STD")
def _std(args, ctx):
    v = _var(args, ctx)
    if isinstance(v, Error):
        return v
    return math.sqrt(v)


@func("SQRT")
def _sqrt(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    if x < 0:
        return ERR
    return math.sqrt(x)


@func("ROUND")
def _round(args, ctx):
    x = to_num(args[0]) if args else ERR
    n = to_num(args[1]) if len(args) > 1 else 0.0
    if isinstance(x, Error):
        return x
    if isinstance(n, Error):
        return n
    factor = 10.0 ** int(n)
    return math.floor(abs(x) * factor + 0.5) / factor * (1 if x >= 0 else -1)


@func("INT")
def _int(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    return float(math.floor(x))


@func("ABS")
def _abs(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    return abs(x)


@func("EXP")
def _exp(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    try:
        return math.exp(x)
    except OverflowError:
        return ERR


@func("LN")
def _ln(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    if x <= 0:
        return ERR
    return math.log(x)


@func("LOG")
def _log(args, ctx):
    x = to_num(args[0]) if args else ERR
    base = to_num(args[1]) if len(args) > 1 else 10.0
    if isinstance(x, Error):
        return x
    if isinstance(base, Error):
        return base
    if x <= 0 or base <= 0 or base == 1:
        return ERR
    return math.log(x, base)


@func("PI")
def _pi(args, ctx):
    return math.pi


@func("SIN")
def _sin(args, ctx):
    x = to_num(args[0]) if args else ERR
    return x if isinstance(x, Error) else math.sin(x)


@func("COS")
def _cos(args, ctx):
    x = to_num(args[0]) if args else ERR
    return x if isinstance(x, Error) else math.cos(x)


@func("TAN")
def _tan(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    try:
        return math.tan(x)
    except ValueError:
        return ERR


@func("ASIN")
def _asin(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    if not -1 <= x <= 1:
        return ERR
    return math.asin(x)


@func("ACOS")
def _acos(args, ctx):
    x = to_num(args[0]) if args else ERR
    if isinstance(x, Error):
        return x
    if not -1 <= x <= 1:
        return ERR
    return math.acos(x)


@func("ATAN")
def _atan(args, ctx):
    x = to_num(args[0]) if args else ERR
    return x if isinstance(x, Error) else math.atan(x)


@func("ATAN2")
def _atan2(args, ctx):
    y = to_num(args[0]) if args else ERR
    x = to_num(args[1]) if len(args) > 1 else ERR
    if isinstance(y, Error):
        return y
    if isinstance(x, Error):
        return x
    return math.atan2(y, x)


@func("MOD")
def _mod(args, ctx):
    a = to_num(args[0]) if args else ERR
    b = to_num(args[1]) if len(args) > 1 else ERR
    if isinstance(a, Error):
        return a
    if isinstance(b, Error):
        return b
    if b == 0:
        return ERR
    return a - b * math.floor(a / b)


@func("RAND")
def _rand(args, ctx):
    return random.random()


# ---------------------------------------------------------------------------
# Logic / lookup
# ---------------------------------------------------------------------------

def _is_range(v):
    return callable(getattr(v, "values", None))


@func("IF")
def _if(args, ctx):
    if not args:
        return ERR
    cond = to_num(_scalar(args[0], ctx))
    if isinstance(cond, Error):
        return cond
    if cond != 0:
        return args[1] if len(args) > 1 else 0.0
    return args[2] if len(args) > 2 else 0.0


def _scalar(v, ctx):
    if _is_range(v):
        return v.scalar(ctx)
    return v


@func("HLOOKUP")
def _hlookup(args, ctx):
    if len(args) < 3:
        return ERR
    key = _scalar(args[0], ctx)
    rng = args[1]
    row_off = to_num(args[2]) if len(args) > 2 else ERR
    if not _is_range(rng) or isinstance(row_off, Error):
        return ERR
    c1, r1, c2, r2 = rng.corners
    if isinstance(key, Error):
        return key
    # exact match wins; otherwise the last column whose key is <= the search key
    # (1-2-3 approximate-match semantics on a sorted row)
    best = None
    for c in range(c1, c2 + 1):
        kv = ctx.val(c, r1)
        if kv is None:
            continue
        try:
            cmp = lotus_compare(key, kv)
        except _Propagate as p:
            return p.err
        if cmp == 0:
            best = c
            break
        if cmp > 0:
            best = c
    if best is None:
        return ERR
    return ctx.val(best, r1 + int(row_off) - 1)


@func("VLOOKUP")
def _vlookup(args, ctx):
    if len(args) < 3:
        return ERR
    key = _scalar(args[0], ctx)
    rng = args[1]
    col_off = to_num(args[2])
    if not _is_range(rng) or isinstance(col_off, Error) or isinstance(key, Error):
        return ERR
    c1, r1, c2, r2 = rng.corners
    best = None
    for r in range(r1, r2 + 1):
        kv = ctx.val(c1, r)
        if kv is None:
            continue
        try:
            cmp = lotus_compare(key, kv)
        except _Propagate as p:
            return p.err
        if cmp == 0:
            best = r
            break
        if cmp > 0:
            best = r
    if best is None:
        return ERR
    return ctx.val(c1 + int(col_off) - 1, best)


@func("INDEX")
def _index(args, ctx):
    if not args:
        return ERR
    rng = args[0]
    if not _is_range(rng):
        return rng
    c1, r1, c2, r2 = rng.corners
    row = int(to_num(args[1])) if len(args) > 1 and to_num(args[1]) != 0 else 0
    col = int(to_num(args[2])) if len(args) > 2 and to_num(args[2]) != 0 else 0
    if row == 0 and col == 0:
        return ctx.val(c1, r1)
    if row == 0:
        return ctx.val(min(c1 + col - 1, c2), r1)
    if col == 0:
        return ctx.val(c1, min(r1 + row - 1, r2))
    return ctx.val(min(c1 + col - 1, c2), min(r1 + row - 1, r2))


@func("CHOOSE")
def _choose(args, ctx):
    if len(args) < 2:
        return ERR
    i = to_num(args[0])
    if isinstance(i, Error):
        return i
    i = int(i)
    if not 1 <= i <= len(args) - 1:
        return ERR
    return _scalar(args[i], ctx)


@func("ISERR")
def _iserr(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    return 1.0 if iserr(v) else 0.0


@func("ISNA")
def _isna(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    return 1.0 if isna(v) else 0.0


@func("ISNUMBER")
def _isnumber(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    return 1.0 if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


@func("ISSTRING")
def _isstring(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    return 1.0 if isinstance(v, str) else 0.0


@func("ERR")
def _err(args, ctx):
    return ERR


@func("NA")
def _na(args, ctx):
    return NA


# ---------------------------------------------------------------------------
# String / date
# ---------------------------------------------------------------------------

@func("LENGTH")
def _length(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    return float(len(to_text(v)))


@func("STRING")
def _string(args, ctx):
    if not args:
        return ERR
    v = to_num(_scalar(args[0], ctx))
    dec = int(to_num(args[1])) if len(args) > 1 else 2
    if isinstance(v, Error):
        return v
    return f"{v:.{max(0, dec)}f}"


@func("VALUE")
def _value(args, ctx):
    v = _scalar(args[0], ctx) if args else None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if v is None:
        return 0.0
    if isinstance(v, Error):
        return v
    s = str(v).strip()
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        x = float(s)
    except ValueError:
        return ERR
    return x / 100.0 if pct else x


@func("LEFT")
def _left(args, ctx):
    s = to_text(_scalar(args[0], ctx)) if args else ""
    n = int(to_num(args[1])) if len(args) > 1 else 1
    if n < 0:
        return ERR
    return s[:n]


@func("RIGHT")
def _right(args, ctx):
    s = to_text(_scalar(args[0], ctx)) if args else ""
    n = int(to_num(args[1])) if len(args) > 1 else 1
    if n < 0:
        return ERR
    return s[-n:] if n else ""


@func("MID")
def _mid(args, ctx):
    if len(args) < 3:
        return ERR
    s = to_text(_scalar(args[0], ctx))
    start = int(to_num(args[1]))
    length = int(to_num(args[2]))
    if start < 1 or length < 0:
        return ERR
    return s[start - 1:start - 1 + length]


@func("UPPER")
def _upper(args, ctx):
    return to_text(_scalar(args[0], ctx) if args else "").upper()


@func("LOWER")
def _lower(args, ctx):
    return to_text(_scalar(args[0], ctx) if args else "").lower()


@func("TRIM")
def _trim(args, ctx):
    return " ".join(to_text(_scalar(args[0], ctx) if args else "").split())


@func("REPEAT")
def _repeat(args, ctx):
    s = to_text(_scalar(args[0], ctx)) if args else ""
    n = int(to_num(args[1])) if len(args) > 1 else 0
    if n < 0 or n > 4096:
        return ERR
    return s * n


@func("CHAR")
def _char(args, ctx):
    n = int(to_num(args[0])) if args else ERR
    if isinstance(n, Error) or not 0 <= n <= 255:
        return ERR
    return chr(n)


@func("CODE")
def _code(args, ctx):
    s = to_text(_scalar(args[0], ctx)) if args else ""
    if not s:
        return ERR
    return float(ord(s[0]))


@func("DATE")
def _date(args, ctx):
    if len(args) < 3:
        return ERR
    y = int(to_num(args[0]))
    m = int(to_num(args[1]))
    d = int(to_num(args[2]))
    if y < 100:
        y += 1900
    try:
        return float(_fmt.date_to_lotus(_dt.date(y, m, d)))
    except ValueError:
        return ERR


@func("DATEVALUE")
def _datevalue(args, ctx):
    s = to_text(_scalar(args[0], ctx) if args else "").strip()
    for fmtstr in ("%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%d-%b-%y",
                   "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m-%d-%Y"):
        try:
            d = _dt.datetime.strptime(s.upper() if "-" in fmtstr and "b" in fmtstr else s, fmtstr)
            return float(_fmt.date_to_lotus(d.date()))
        except ValueError:
            continue
    return ERR


@func("NOW")
def _now(args, ctx):
    return _fmt.datetime_to_serial(_dt.datetime.now())


@func("TODAY")
def _today(args, ctx):
    return float(_fmt.date_to_lotus(_dt.date.today()))


@func("TIME")
def _time(args, ctx):
    if len(args) < 3:
        return ERR
    h = int(to_num(args[0]))
    m = int(to_num(args[1]))
    s = int(to_num(args[2]))
    total = h * 3600 + m * 60 + s
    return (total % 86400) / 86400.0


@func("YEAR")
def _year(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.lotus_to_date(v).year)


@func("MONTH")
def _month(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.lotus_to_date(v).month)


@func("DAY")
def _day(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.lotus_to_date(v).day)


@func("HOUR")
def _hour(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.serial_to_datetime(v).hour)


@func("MINUTE")
def _minute(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.serial_to_datetime(v).minute)


@func("SECOND")
def _second(args, ctx):
    v = to_num(_scalar(args[0], ctx)) if args else ERR
    if isinstance(v, Error):
        return v
    return float(_fmt.serial_to_datetime(v).second)


# ---------------------------------------------------------------------------
# Financial
# ---------------------------------------------------------------------------

@func("PV")
def _pv(args, ctx):
    rate = to_num(args[0]) if len(args) > 0 else ERR
    nper = to_num(args[1]) if len(args) > 1 else ERR
    pmt = to_num(args[2]) if len(args) > 2 else 0.0
    if isinstance(rate, Error) or isinstance(nper, Error) or isinstance(pmt, Error):
        return ERR
    if rate == 0:
        return -(pmt * nper)
    return -(pmt * (1 - (1 + rate) ** -nper) / rate)


@func("FV")
def _fv(args, ctx):
    rate = to_num(args[0]) if len(args) > 0 else ERR
    nper = to_num(args[1]) if len(args) > 1 else ERR
    pmt = to_num(args[2]) if len(args) > 2 else 0.0
    if isinstance(rate, Error) or isinstance(nper, Error) or isinstance(pmt, Error):
        return ERR
    if rate == 0:
        return -(pmt * nper)
    return -(pmt * ((1 + rate) ** nper - 1) / rate)


@func("PAYMENT")
def _payment(args, ctx):
    rate = to_num(args[0]) if len(args) > 0 else ERR
    nper = to_num(args[1]) if len(args) > 1 else ERR
    pv = to_num(args[2]) if len(args) > 2 else ERR
    fv = to_num(args[3]) if len(args) > 3 else 0.0
    if any(isinstance(x, Error) for x in (rate, nper, pv, fv)):
        return ERR
    if rate == 0:
        return -(pv + fv) / nper
    return -(pv * (1 + rate) ** nper + fv) * rate / ((1 + rate) ** nper - 1)


@func("NPV")
def _npv(args, ctx):
    if not args:
        return ERR
    rate = to_num(args[0])
    if isinstance(rate, Error):
        return rate
    total = 0.0
    n = 1
    for v in flatten(args[1:]):
        x = to_num(v)
        if isinstance(x, Error):
            continue
        total += x / (1 + rate) ** n
        n += 1
    return total


@func("IRR")
def _irr(args, ctx):
    if not args:
        return ERR
    guess = to_num(args[1]) if len(args) > 1 else 0.1
    if isinstance(guess, Error):
        return guess
    flows = []
    for v in flatten([args[0]]):
        x = to_num(v)
        if isinstance(x, Error):
            continue
        flows.append(x)
    if len(flows) < 2:
        return ERR

    def npv(r):
        return sum(cf / (1 + r) ** i for i, cf in enumerate(flows))

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return ERR
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-10:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


@func("TERM")
def _term(args, ctx):
    """@TERM(rate, pv, fv): periods for pv to grow to -fv at rate (closed form)."""
    rate = to_num(args[0]) if len(args) > 0 else ERR
    pv = to_num(args[1]) if len(args) > 1 else ERR
    fv = to_num(args[2]) if len(args) > 2 else ERR
    if any(isinstance(x, Error) for x in (rate, pv, fv)):
        return ERR
    if rate <= -1 or pv == 0 or fv == 0:
        return ERR
    ratio = fv / pv
    if ratio <= 0:
        return ERR
    return math.log(ratio) / math.log(1 + rate)


@func("RATE")
def _rate(args, ctx):
    nper = to_num(args[0]) if len(args) > 0 else ERR
    pmt = to_num(args[1]) if len(args) > 1 else ERR
    pv = to_num(args[2]) if len(args) > 2 else ERR
    fv = to_num(args[3]) if len(args) > 3 else 0.0
    if any(isinstance(x, Error) for x in (nper, pmt, pv, fv)) or nper == 0:
        return ERR

    def f(r):
        if r == 0:
            return pv + pmt * nper + fv
        return pv * (1 + r) ** nper + pmt * ((1 + r) ** nper - 1) / r + fv

    lo, hi = -0.9999, 10.0
    try:
        flo, fhi = f(lo), f(hi)
    except (OverflowError, ZeroDivisionError):
        return ERR
    if flo * fhi > 0:
        return ERR
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-10:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Misc database helpers used by data_cmd (exposed for @functions parity)
# ---------------------------------------------------------------------------

@func("CELL")
def _cell_fn(args, ctx):
    from .grid import col_letters, format_address
    attr = to_text(_scalar(args[0], ctx)).upper() if args else ""
    ref = args[1] if len(args) > 1 else None
    if getattr(ref, "_is_ref", False):
        c, r = ref.c, ref.r
    else:
        c, r = ctx.fkey
    cell = ctx.engine.sheet.cell(c, r)
    kind = cell.kind if cell is not None else "EMPTY"
    if attr == "ADDRESS":
        return format_address(c, r)
    if attr == "ROW":
        return float(r + 1)
    if attr == "COL":
        return col_letters(c)
    if attr == "TYPE":
        return {"LABEL": "label", "NUMBER": "number", "FORMULA": "formula",
                "EMPTY": "empty"}.get(kind, "empty")
    if attr == "CONTENTS":
        return cell.raw if cell is not None and cell.raw is not None else ""
    if attr == "PREFIX":
        if cell is not None and cell.raw and cell.raw[0] in "'^\"":
            return cell.raw[0]
        return ctx.engine.sheet.label_prefix if kind == "LABEL" else ""
    if attr == "PROTECT":
        return "protected" if (cell is not None and cell.protected) else "unprotected"
    if attr == "WIDTH":
        return float(ctx.engine.sheet.col_width(c))
    if attr == "FORMAT":
        f = cell.fmt if cell is not None else None
        return (f or {}).get("type", "general")
    return ERR


@func("COLS")
def _cols(args, ctx):
    rng = args[0] if args else None
    if callable(getattr(rng, "values", None)):
        c1, r1, c2, r2 = rng.corners
        return float(c2 - c1 + 1)
    return 1.0


@func("ROWS")
def _rows(args, ctx):
    rng = args[0] if args else None
    if callable(getattr(rng, "values", None)):
        c1, r1, c2, r2 = rng.corners
        return float(r2 - r1 + 1)
    return 1.0
