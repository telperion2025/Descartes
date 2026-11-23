"""Extensive edge-case battery: addressing, parser/engine, functions, fmt,
persist, data commands, app interaction, raw input, macros, screen.

Error values are checked via the duck marker `_is_lotus_error` because make_app
reimports descartes modules, so class identity differs across the suite.
"""

import math
import struct

import pytest

from conftest import char, draw, make_app  # noqa: F401  (fixture import)
from conftest import press, screen_text, type_text
from descartes import fmt as _fmt
from descartes import persist as _persist
from descartes.engine import (Engine, FormulaSyntaxError, compile_node, parse,
                              serialize)
from descartes.grid import (EMPTY, FORMULA, LABEL, MAX_COLS, MAX_ROWS, NUMBER,
                            Worksheet, classify_entry, col_index, col_letters,
                            format_address, format_range, normalize_corners,
                            parse_address, parse_number_literal, range_cells,
                            split_range, split_range_any)


def is_err(v, code="ERR"):
    return getattr(v, "_is_lotus_error", False) and getattr(v, "code", None) == code


def is_na(v):
    return is_err(v, "NA")


def make_ws(entries):
    ws = Worksheet("A")
    eng = Engine(ws)
    for (c, r), raw in entries.items():
        ws.set_entry(c, r, raw)
    eng.register_all()
    eng.recalc(force=True)
    return ws, eng


def val(ws, addr):
    c, r = parse_address(addr)
    return ws.value_at(c, r)


def evalf(raw, ws=None):
    ws = ws or Worksheet("A")
    eng = Engine(ws)
    ast = parse(raw)
    from descartes.engine import Ctx
    return compile_node(ast)(Ctx(eng, (0, 0)))


# ---------------------------------------------------------------------------
# addressing / grid
# ---------------------------------------------------------------------------

class TestAddressingBattery:
    def test_col_letters_boundaries(self):
        assert col_letters(0) == "A"
        assert col_letters(25) == "Z"
        assert col_letters(26) == "AA"
        assert col_letters(51) == "AZ"
        assert col_letters(52) == "BA"
        assert col_letters(MAX_COLS - 1) == "IV"

    def test_col_index_roundtrip(self):
        for c in (0, 1, 25, 26, 27, 51, 52, MAX_COLS - 1):
            assert col_index(col_letters(c)) == c

    def test_col_index_letters_only(self):
        with pytest.raises(ValueError):
            col_index("A1")
        with pytest.raises(ValueError):
            col_index("")

    def test_parse_address_variants(self):
        assert parse_address("$A$1") == (0, 0)
        assert parse_address("a1") == (0, 0)
        assert parse_address(" IV8192 ") == (MAX_COLS - 1, MAX_ROWS - 1)
        assert parse_address("$iv$8192") == (MAX_COLS - 1, MAX_ROWS - 1)

    def test_parse_address_rejects(self):
        for bad in ("A0", "A8193", "A", "1A", "A-1", "ZZ1", "AAAA1", "", " ",
                    "A1B", "!A1", "A!1"):
            with pytest.raises(ValueError):
                parse_address(bad)

    def test_format_address_roundtrip(self):
        for a in ("A1", "IV8192", "AZ10", "$B$2"):
            assert format_address(*parse_address(a)) == a.replace("$", "")
        with pytest.raises(ValueError):
            parse_address("ZZ100")     # col 701 >= MAX_COLS

    def test_split_range_variants(self):
        assert split_range("B2") == ((1, 1), (1, 1))
        assert split_range("B2..A1") == ((0, 0), (1, 1))   # normalizes
        assert split_range("$A$1..$B$2") == ((0, 0), (1, 1))
        with pytest.raises(ValueError):
            split_range("A1..B2..C3")
        with pytest.raises(ValueError):
            split_range("foo")

    def test_split_range_any_3d(self):
        assert split_range_any("A:C!A1..B2") == (("A", "C"), (0, 0), (1, 1))
        assert split_range_any("B!A1") == (("B", "B"), (0, 0), (0, 0))
        assert split_range_any("$A:$C!$A$1..$B$2") == (("A", "C"), (0, 0), (1, 1))
        assert split_range_any("A1..B2")[0] is None

    def test_range_cells_bounds(self):
        assert list(range_cells((0, 0), (0, 0))) == [(0, 0)]
        assert len(list(range_cells((0, 0), (2, 2)))) == 9
        assert list(range_cells((2, 2), (0, 0))) == list(range_cells((0, 0), (2, 2)))

    def test_normalize_corners(self):
        assert normalize_corners((5, 5), (1, 2)) == ((1, 2), (5, 5))

    def test_format_range(self):
        assert format_range((0, 0), (1, 1)) == "A1..B2"

    def test_parse_number_literal(self):
        assert parse_number_literal("42") == 42.0
        assert parse_number_literal("-3.5") == -3.5
        assert parse_number_literal("+7") == 7.0
        assert parse_number_literal("50%") == 0.5
        assert parse_number_literal("1e3") == 1000.0
        assert parse_number_literal(".5") == 0.5
        assert parse_number_literal("inf") is None
        assert parse_number_literal("nan") is None
        assert parse_number_literal("1e999") is None      # overflow -> not a number
        assert parse_number_literal("abc") is None
        assert parse_number_literal("") is None
        assert parse_number_literal("1,000") is None
        assert parse_number_literal("12:30") is None

    def test_classify_entry_edges(self):
        assert classify_entry("")[0] == EMPTY
        assert classify_entry(None)[0] == EMPTY
        assert classify_entry("'")[0] == LABEL
        assert classify_entry("'abc")[0] == LABEL
        assert classify_entry("^c")[0] == LABEL
        assert classify_entry('"r')[0] == LABEL
        assert classify_entry("\\rep")[0] == LABEL
        assert classify_entry("42")[0] == NUMBER
        assert classify_entry("-2.5")[0] == NUMBER
        assert classify_entry("50%")[0] == NUMBER
        assert classify_entry("+")[0] == FORMULA
        assert classify_entry("=")[0] == LABEL      # '=' is not a formula indicator in 1-2-3
        assert classify_entry("+A1")[0] == FORMULA
        assert classify_entry("@SUM(A1)")[0] == FORMULA
        assert classify_entry("hello")[0] == LABEL
        assert classify_entry(" inf")[0] == LABEL

    def test_set_entry_out_of_range(self):
        ws = Worksheet("A")
        with pytest.raises(ValueError):
            ws.set_entry(-1, 0, "1")
        with pytest.raises(ValueError):
            ws.set_entry(0, MAX_ROWS, "1")
        with pytest.raises(ValueError):
            ws.set_entry(MAX_COLS, 0, "1")

    def test_set_entry_empty_clears(self):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "5")
        ws.set_entry(0, 0, "")
        assert ws.cell(0, 0) is None

    def test_label_with_newline_and_unicode(self):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "'héllo\nwörld")
        cell = ws.cell(0, 0)
        assert cell.kind == LABEL
        assert cell.value == "héllo\nwörld"


# ---------------------------------------------------------------------------
# parser / engine
# ---------------------------------------------------------------------------

class TestParserBattery:
    def test_empty_and_bare_formulas(self):
        for bad in ("+", "+()", "+@", "+A1..", "+..A1", "+A1:B2:C3", "+@SUM(",
                    "+1 2", "+\"unterminated", "+)", "+(", "+A1)"):
            with pytest.raises(FormulaSyntaxError):
                parse(bad)
        assert is_err(evalf("+@FOO(1)"))   # unknown function parses, evals to ERR

    def test_unary_chains(self):
        assert evalf("+-+-+5") == 5.0    # leading + is the indicator; -+-+5 == 5
        assert evalf("+--5") == 5.0
        assert evalf("+#NOT#0") == 1.0

    def test_power_associativity(self):
        assert evalf("+2^3^2") == 64.0   # ^ is left-associative, as in 1-2-3

    def test_precedence_full_chain(self):
        ws, _ = make_ws({(0, 0): "+1+2*3^2-4/2"})
        assert val(ws, "A1") == 1 + 2 * 9 - 2

    def test_comparison_and_logic(self):
        assert evalf("+1=1") == 1.0
        assert evalf("+1<>2") == 1.0
        assert evalf("+1 #AND# 0") == 0.0
        assert evalf("+1 #OR# 0") == 1.0
        assert evalf("+1<2=1") == 1.0    # (1<2)=1 -> 1=1

    def test_concat(self):
        assert evalf('+"a"&"b"&1') == "ab1"

    def test_string_escapes(self):
        assert evalf('+"a""b"') == 'a"b'

    def test_name_vs_cell_ambiguity(self):
        ws, eng = make_ws({})
        eng.wb = None
        # A1 is a cell, AA1 is a cell; a pure-alpha token is a name -> ERR
        assert is_err(evalf("+TOTAL", ws))

    def test_percent_and_parens(self):
        assert evalf("+50%") == 0.5
        assert evalf("+(1+2)*3") == 9.0

    def test_3d_syntax_parses(self):
        ast = parse("+A:C!A1..B2")
        assert ast[0] == "range3d"
        assert serialize(ast) == "A:C!A1..B2"
        ast2 = parse("+B!A1")
        assert ast2[0] == "cell3d"
        assert serialize(ast2) == "B!A1"

    def test_dollar_preserved(self):
        assert serialize(parse("+$A$1")) == "$A$1"
        assert serialize(parse("+$A1")) == "$A1"
        assert serialize(parse("+A$1")) == "A$1"

    def test_serialize_all_node_kinds(self):
        for raw in ("+1", '+"s"', "+A1", "+A1..B2", "+B!A1", "+A:B!A1..B2",
                    "+(1+2)", "+-1", "+#NOT#1", "+50%", "+@SUM(1,2)", "+TOTAL",
                    '+"a""b"'):
            once = serialize(parse(raw))
            assert serialize(parse("+" + once)) == once   # idempotent

    def test_formula_at_max_cell(self):
        ws, _ = make_ws({(MAX_COLS - 1, MAX_ROWS - 1): "+1+1"})
        assert val(ws, "IV8192") == 2.0

    def test_implicit_intersection_matrix(self):
        entries = {(0, 0): "1", (1, 0): "2", (0, 1): "3", (1, 1): "4"}
        # formula column+row inside range -> exact cell
        ws, _ = make_ws({**entries, (2, 1): "+A1..B2"})
        assert val(ws, "C2") == 4.0
        # same row, outside columns -> clamped column
        ws2, _ = make_ws({**entries, (5, 0): "+A1..B2"})
        assert val(ws2, "F1") == 2.0
        # same column, outside rows -> clamped row
        ws3, _ = make_ws({**entries, (0, 9): "+A1..B2"})
        assert val(ws3, "A10") == 3.0
        # single row range, formula beyond it -> clamped to last col of row
        ws4, _ = make_ws({(0, 0): "10", (1, 0): "20", (2, 0): "+A1..B1"})
        assert val(ws4, "C1") == 20.0

    def test_shift_edges_clamp(self):
        from descartes.engine import shift
        ast = parse("+A1")
        assert serialize(shift(ast, -5, -5)) == "A1"   # clamps at 0
        ast2 = parse("+$A$1")
        assert serialize(shift(ast2, 300, 9000)) == "$A$1"   # absolute refs never shift
        ast3 = parse("+A1")
        assert serialize(shift(ast3, 300, 9000)) == "IV8192"  # clamps at max

    def test_adjust_refs_insert_delete(self):
        from descartes.engine import adjust_refs
        ast = parse("+A1..C3")
        ins = adjust_refs(ast, "row", 1, 2)
        assert serialize(ins) == "A1..C5"
        dele = adjust_refs(ast, "row", 1, -1, delete=True)
        assert serialize(dele) == "A1..C2"
        # cell inside deleted band -> ERR (B3 is row index 2, band [2,3))
        c = parse("+B3")
        assert serialize(adjust_refs(c, "row", 2, -1, delete=True)) == "@ERR()"
        # cell above the band is untouched
        c2 = parse("+B2")
        assert serialize(adjust_refs(c2, "row", 2, -1, delete=True)) == "B2"

    def test_cycle_err_and_iteration(self):
        ws, eng = make_ws({(0, 0): "+B1+1", (1, 0): "+A1+1"})
        assert is_err(val(ws, "A1"))
        ws2 = Worksheet("A")
        ws2.recalc_mode = "auto"
        ws2.iterations = 50
        eng2 = Engine(ws2)
        ws2.set_entry(0, 0, "+A1/2+1")
        eng2.register_all()
        eng2.recalc(force=True)
        assert abs(val(ws2, "A1") - 2.0) < 1e-9

    def test_deep_formula_chain(self):
        n = 500
        entries = {(0, i): f"+A{i}" for i in range(1, n)}
        entries[(0, 0)] = "1"
        ws, eng = make_ws(entries)
        assert val(ws, "A500") == 1.0

    def test_recalc_only_dirty(self):
        ws, eng = make_ws({(0, 0): "1", (1, 0): "+A1*2", (2, 0): "+B1*2"})
        assert val(ws, "C1") == 4.0
        ws.set_entry(0, 0, "10")
        eng.note_edit((0, 0))
        eng.recalc()
        assert val(ws, "C1") == 40.0
        # manual mode: no recalc unless forced
        ws.recalc_mode = "manual"
        ws.set_entry(0, 0, "100")
        eng.note_edit((0, 0))
        assert eng.recalc() is False
        assert val(ws, "C1") == 40.0
        eng.recalc(force=True)
        assert val(ws, "C1") == 400.0

    def test_unregister_stale_formula(self):
        ws, eng = make_ws({(0, 0): "+B1", (1, 0): "5"})
        assert val(ws, "A1") == 5.0
        ws.set_entry(0, 0, "7")
        eng.register_all()
        eng.recalc(force=True)
        assert val(ws, "A1") == 7.0
        assert (0, 0) not in eng.formulas


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------

class TestFunctionsBattery:
    def test_math_edges(self):
        assert is_err(evalf("+@SQRT(-1)"))
        assert is_err(evalf("+@LOG(-1)"))
        assert is_err(evalf("+@LN(0)"))
        assert evalf("+@ABS(-3)") == 3.0
        assert evalf("+@INT(-3.7)") == -4.0
        assert evalf("+@MOD(7,3)") == 1.0
        assert is_err(evalf("+@MOD(1,0)"))
        assert evalf("+@ROUND(2.5,0)") == 3.0
        assert evalf("+@ROUND(123.456,2)") == 123.46
        assert is_err(evalf("+@ASIN(2)"))
        assert evalf("+@ATAN2(1,1)") == pytest.approx(math.pi / 4)
        assert evalf("+@PI()") == pytest.approx(math.pi)

    def test_aggregate_edges(self):
        assert evalf("+@SUM()") == 0.0
        assert is_err(evalf("+@AVG()"))
        assert evalf("+@COUNT()") == 0.0
        assert evalf("+@MAX()") == 0.0   # empty aggregate -> 0
        assert is_err(evalf("+@SUM(1,\"x\",3)"))   # literal string arg -> ERR
        assert evalf("+@MIN(2,1,3)") == 1.0
        assert is_err(evalf("+@STD(2)"))     # sample std of one value: undefined
        assert is_err(evalf("+@STD()"))

    def test_string_edges(self):
        assert evalf('+@LEFT("hello",2)') == "he"
        assert evalf('+@LEFT("hi",0)') == ""
        assert evalf('+@LEFT("hi",99)') == "hi"
        assert evalf('+@RIGHT("hello",2)') == "lo"
        assert is_err(evalf('+@LEFT("hi",-1)'))
        assert evalf('+@MID("hello",2,3)') == "ell"
        assert is_err(evalf('+@MID("hi",0,1)'))
        assert evalf('+@LENGTH("abc")') == 3.0
        assert evalf('+@UPPER("aBc")') == "ABC"
        assert evalf('+@LOWER("AbC")') == "abc"
        assert evalf('+@PROPER("hello world")') == "Hello World"
        assert evalf('+@TRIM("  a  b  ")') == "a b"
        assert evalf('+@REPEAT("ab",3)') == "ababab"
        assert evalf('+@REPEAT("x",0)') == ""
        assert evalf('+@CHAR(65)') == "A"
        assert evalf('+@CODE("A")') == 65.0
        assert evalf('+@EXACT("a","a")') == 1.0
        assert evalf('+@EXACT("a","A")') == 0.0
        assert is_err(evalf('+@FIND("z","abc")'))
        assert evalf('+@FIND("b","abc")') == 2.0
        assert is_err(evalf('+@FIND("b","abc",3)'))
        assert evalf('+@EXACT("a","A")') == 0.0
        assert evalf('+@CLEAN("a\x01b")') == "ab"
        assert evalf('+@REPLACE("abcdef",2,3,"XY")') == "aXYef"
        assert evalf('+@PROPER("hello world")') == "Hello World"
        assert evalf('+@VALUE("3.5")') == 3.5
        assert is_err(evalf('+@VALUE("abc")'))
        assert evalf('+@STRING(3.14159,2)') == "3.14"

    def test_lookup_edges(self):
        # keys vertical in col A, values in col B, formula outside the range
        ws, _ = make_ws({(0, 0): "1", (1, 0): "10", (0, 1): "2", (1, 1): "20",
                         (3, 0): "+@VLOOKUP(2,A1..B2,2)"})
        assert val(ws, "D1") == 20.0            # exact match
        ws2, _ = make_ws({(0, 0): "1", (1, 0): "10",
                          (3, 0): "+@VLOOKUP(0.5,A1..B2,2)"})
        assert is_err(val(ws2, "D1"))           # key below all keys -> ERR
        ws3, _ = make_ws({(0, 0): "1", (1, 0): "10", (0, 1): "5", (1, 1): "50",
                          (3, 0): "+@VLOOKUP(3,A1..B2,2,1)"})
        assert val(ws3, "D1") == 10.0           # approximate: last key <= 3
        ws4, _ = make_ws({(0, 0): "x", (1, 0): "2", (2, 0): "y",
                          (0, 2): "+@HLOOKUP(2,A1..C1,1)"})
        assert val(ws4, "A3") == 2.0

    def test_choose_index_edges(self):
        assert evalf('+@CHOOSE(2,"a","b","c")') == "b"
        assert is_err(evalf('+@CHOOSE(0,"a")'))
        assert is_err(evalf('+@CHOOSE(9,"a")'))

    def test_index_edges(self):
        ws, _ = make_ws({(0, 0): "5", (1, 0): "6",
                         (0, 1): "+@INDEX(A1..B1,1,2)"})
        assert val(ws, "A2") == 6.0
        ws2, _ = make_ws({(0, 0): "5", (0, 1): "+@INDEX(A1..A1,1,9)"})
        assert is_err(val(ws2, "A2"))

    def test_if_edges(self):
        assert evalf('+@IF(1,"a","b")') == "a"
        assert evalf('+@IF(0,"a","b")') == "b"
        assert is_err(evalf('+@IF("x","a","b")'))    # string cond -> ERR, as in 1-2-3
        assert is_err(evalf('+@IF("","a","b")'))
        assert is_err(evalf("+@IF(@ERR(),1,2)"))    # error propagates

    def test_logical_predicates(self):
        assert evalf("+@ISNUMBER(1)") == 1.0
        assert evalf('+@ISNUMBER("x")') == 0.0
        assert evalf('+@ISSTRING("x")') == 1.0
        assert evalf("+@ISERR(@ERR())") == 1.0
        assert evalf("+@ISERR(1)") == 0.0
        assert evalf("+@ISNA(@NA())") == 1.0
        assert is_na(evalf("+@NA()"))
        assert is_err(evalf("+@ERR()"))

    def test_date_time_edges(self):
        # Lotus 1900 leap-year bug: 1900-02-29 exists as serial 60
        assert evalf("+@DATE(1900,2,29)") == 60.0
        assert evalf("+@DATE(1900,3,1)") == 61.0
        assert evalf("+@YEAR(61)") == 1900.0
        assert evalf("+@MONTH(61)") == 3.0
        assert evalf("+@DAY(61)") == 1.0
        assert evalf("+@TIME(12,0,0)") == 0.5
        assert evalf("+@HOUR(0.5)") == 12.0
        assert evalf("+@MINUTE(0.5)") == 0.0
        assert evalf("+@SECOND(0.5)") == 0.0
        assert evalf("+@DATEVALUE(\"1/1/1900\")") == 1.0
        assert 40000 < evalf("+@TODAY()") < 60000
        assert 40000 < evalf("+@NOW()") < 80000

    def test_financial_edges(self):
        assert evalf("+@PV(0.1,10,-100)") == pytest.approx(614.457, rel=1e-3)
        # implemented convention: @FV/@PV(rate, nper, pmt), @PAYMENT(rate, nper, pv)
        assert evalf("+@FV(0.1,10,-100)") == pytest.approx(100 * (1.1 ** 10 - 1) / 0.1, rel=1e-9)
        assert evalf("+@PAYMENT(0.1,10,100)") == pytest.approx(-(100 * 1.1 ** 10) * 0.1 / (1.1 ** 10 - 1), rel=1e-9)
        assert evalf("+@TERM(0.1,100,1000)") == pytest.approx(math.log(10) / math.log(1.1), rel=1e-9)
        evalf("+@RATE(10,-100,1000)")   # must not raise

    def test_cols_rows_n_s(self):
        ws, _ = make_ws({(0, 0): "5", (1, 0): "6", (4, 4): "+@COLS(A1..C2)"})
        assert val(ws, "E5") == 3.0
        ws2, _ = make_ws({(0, 0): "'txt", (4, 0): "+@ROWS(A1..C3)"})
        assert val(ws2, "E1") == 3.0
        ws3, _ = make_ws({(0, 0): "'lbl", (1, 0): "+@S(A1)"})
        assert val(ws3, "B1") == "lbl"
        ws4, _ = make_ws({(0, 0): "42", (1, 0): "+@N(A1)"})
        assert val(ws4, "B1") == 42.0

    def test_cell_pointer_and_cell(self):
        ws, eng = make_ws({(0, 0): "5"})
        # evalf evaluates with fkey (0,0) = A1
        assert evalf('+@CELLPOINTER("TYPE")', ws) == "number"
        assert evalf('+@CELLPOINTER("ADDRESS")', ws) == "A1"
        assert evalf('+@CELLPOINTER("ROW")', ws) == 1.0
        assert evalf('+@CELLPOINTER("COL")', ws) == "A"
        assert evalf('+@CELLPOINTER("CONTENTS")', ws) == "5"
        assert evalf('+@CELL("TYPE",A1)', ws) == "number"
        assert is_err(evalf('+@CELLPOINTER("BOGUS")', ws))

    def test_rand_in_range(self):
        for _ in range(20):
            v = evalf("+@RAND()")
            assert 0.0 <= v < 1.0

    def test_unknown_function_is_err(self):
        assert is_err(evalf("+@FOO(1)"))

    def test_wrong_arity_no_crash(self):
        for raw in ("+@ABS()", "+@ABS(1,2)", "+@SUM(1,", "+@IF(1)", "+@DATE(1)",
                    "+@LEFT(\"a\")", "+@MOD(1)", "+@CHOOSE(1)", "+@VLOOKUP(1)"):
            try:
                parse(raw)
            except FormulaSyntaxError:
                continue
            evalf(raw)   # must not raise


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

class TestFormatBattery:
    def test_general_number_edges(self):
        g = _fmt.general_number
        assert g(0.0) == "0"
        assert g(-0.0) == "0"
        assert g(5.0) == "5"
        assert g(-42.0) == "-42"
        assert g(1e14) == "100000000000000"
        assert g(999999999999999.0) == "999999999999999"
        assert g(1e15) == "1e+15"          # beyond int-safe range -> exponent
        assert g(0.1 + 0.2) == "0.3"
        assert g(float("inf")) == "ERR"
        assert g(float("-inf")) == "ERR"
        assert g(float("nan")) == "ERR"

    def test_format_number_types(self):
        fn = _fmt.format_number
        assert fn(1234.5678, {"type": "fixed", "decimals": 2}) == "1234.57"
        assert fn(1234.5678, {"type": "fixed", "decimals": 0}) == "1235"
        assert fn(-5, {"type": "currency", "decimals": 2}) == "$-5.00"
        assert fn(1234567.891, {"type": "comma", "decimals": 2}) == "1,234,567.89"
        assert fn(0.1234, {"type": "percent", "decimals": 1}) == "12.3%"
        assert fn(1234.5678, {"type": "sci", "decimals": 2}) == "1.23E+03"
        assert fn(0.0001234, {"type": "sci", "decimals": 3}) == "1.234E-04"
        assert fn(5, {"type": "hidden"}) == ""
        assert fn(5, {"type": "text"}) == "5"
        assert fn(5, None) == "5"
        assert fn(float("inf"), None) == "ERR"
        assert fn(7, {"type": "bogus"}) == "7"   # unknown type -> general

    def test_date_formats_and_phantom_day(self):
        fd = _fmt.format_date
        assert fd(1) == "01-Jan-00"
        assert fd(59) == "28-Feb-00"
        assert fd(60) == "29-Feb-00"            # fictitious 1900-02-29
        assert fd(60, "dd/mm/yyyy") == "29/02/1900"
        assert fd(61) == "01-Mar-00"
        assert fd(36526, "dd/mm/yyyy") == "01/01/2000"
        assert fd(36526, "mm/dd/yy") == "01/01/00"
        assert fd(36526, "dd-mmm-yyyy") == "01-Jan-2000"
        assert fd(36526, "mmm-yy") == "Jan-00"
        assert fd(36526, "dd-mmm") == "01-Jan"
        assert fd(-1) == "ERR" or fd(-1) != ""  # negative serial handled, no crash
        assert _fmt.lotus_to_ymd(60) == (1900, 2, 29)
        assert _fmt.lotus_to_ymd(61) == (1900, 3, 1)

    def test_date_serial_roundtrip(self):
        import datetime as dt
        for d in (dt.date(1900, 1, 1), dt.date(1900, 2, 28), dt.date(1900, 3, 1),
                  dt.date(1999, 12, 31), dt.date(2000, 2, 29), dt.date(2024, 6, 15),
                  dt.date(2100, 1, 1)):
            assert _fmt.lotus_to_date(_fmt.date_to_lotus(d)) == d

    def test_time_formats(self):
        assert _fmt.format_time(0.5) == "12:00:00"
        assert _fmt.format_time(0.5, "hh:mm") == "12:00"
        assert _fmt.format_time(0.0) == "00:00:00"
        assert _fmt.format_time(0.25) == "06:00:00"
        assert _fmt.format_time(1.5) == "12:00:00"   # date part ignored

    def test_display_edges(self):
        from descartes.functions import ERR, NA
        assert _fmt.display(None, None) == ""
        assert _fmt.display("abc", None) == "abc"
        assert _fmt.display(ERR, None) == "ERR"
        assert _fmt.display(NA, None) == "NA"
        assert _fmt.display(1.5, {"type": "fixed", "decimals": 1}) == "1.5"
        assert _fmt.display("x", {"type": "hidden"}) == ""
        assert _fmt.display(True, None) == "1"
        assert _fmt.display(2, {"type": "text"}) == "2"

    def test_wk1_byte_roundtrip(self):
        for byte in range(0, 80):
            m = _fmt.wk1_byte_to_fmt(byte)
            assert _fmt.fmt_to_wk1_byte(m) == byte, byte
        assert _fmt.wk1_byte_to_fmt(0) == {"type": "fixed", "decimals": 0}
        assert _fmt.wk1_byte_to_fmt(2)["type"] == "fixed"
        assert _fmt.wk1_byte_to_fmt(0x80 | 2) == {"type": "fixed", "decimals": 2,
                                                  "protected": True}


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def _rec(op, payload):
    return struct.pack("<HH", op, len(payload)) + payload


def _wk3_bof():
    return _rec(0x0000, struct.pack("<HH", 0x1000, 0x0004) + b"\x00" * 22)


class TestPersistBattery:
    def test_vg1_roundtrip_weird_cells(self, tmp_path):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "'héllo → wörld")
        ws.set_entry(1, 0, "+@ERR()")
        ws.set_entry(2, 0, "+1/0")
        ws.set_entry(0, 1, "3.14159")
        ws.set_entry(3, 1, "'")
        ws2 = Worksheet("B")
        ws2.set_entry(0, 0, "+A1")
        path = str(tmp_path / "w.vg1")
        _persist.save_vg1(path, {"A": ws, "B": ws2},
                          names={"R3D": {"sheet": "A", "sheets": ["A", "B"],
                                         "range": ((0, 0), (1, 1))},
                                 "SIMPLE": {"sheet": "B", "range": ((0, 0), (0, 0))}},
                          cursor=(1, 1), recalc_mode="manual", iterations=25,
                          graphs=[], print_settings={"lines_page": 50}, active="B")
        data = _persist.load_vg1(path)
        a = data["sheets"]["A"]
        assert a.value_at(0, 0) == "héllo → wörld"
        assert a.raw_at(1, 0) == "+@ERR()"
        assert a.kind_at(2, 0) == FORMULA
        assert a.value_at(0, 1) == 3.14159
        assert a.raw_at(3, 1) == "'"
        assert data["sheets"]["B"].raw_at(0, 0) == "+A1"
        assert data["names"]["R3D"]["sheets"] == ["A", "B"]
        assert data["cursor"] == (1, 1)
        assert data["recalc_mode"] == "manual"
        assert data["iterations"] == 25

    def test_vg1_rejects_garbage(self, tmp_path):
        p1 = tmp_path / "bad1.vg1"
        p1.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            _persist.load_vg1(str(p1))          # JSONDecodeError is a ValueError
        p2 = tmp_path / "bad2.vg1"
        p2.write_text('{"format": "other", "version": 1}', encoding="utf-8")
        with pytest.raises(ValueError):
            _persist.load_vg1(str(p2))

    def test_wk1_crafted_records(self, tmp_path):
        num = _rec(0x000E, struct.pack("<BHH", 0, 2, 3) + struct.pack("<d", 42.5))
        integer = _rec(0x000D, struct.pack("<BHHh", 0, 1, 0, -7))
        label = _rec(0x000F, struct.pack("<BHH", 0, 0, 0) + b"'" + b"hi\0")
        colw = _rec(0x0008, struct.pack("<HB", 0, 12))
        unknown = _rec(0x00FE, b"abc")
        bof = _rec(0x0000, struct.pack("<HH", 0x0404, 0))
        eof = _rec(0x0001, b"")
        path = tmp_path / "t.wk1"
        path.write_bytes(bof + unknown + num + integer + label + colw + eof)
        data = _persist.read_wk1(str(path))
        ws = data["sheets"]["A"]
        assert ws.value_at(2, 3) == 42.5
        assert ws.value_at(1, 0) == -7.0
        assert ws.value_at(0, 0) == "hi"
        assert ws.raw_at(0, 0) == "'hi"
        assert ws.col_width(0) == 12

    def test_wk1_truncated_is_graceful(self, tmp_path):
        num = _rec(0x000E, struct.pack("<BHH", 0, 2, 3) + struct.pack("<d", 42.5))
        bof = _rec(0x0000, struct.pack("<HH", 0x0404, 0))
        path = tmp_path / "trunc.wk1"
        path.write_bytes(bof + num[:6])     # cut mid-record
        data = _persist.read_wk1(str(path))
        assert data["sheets"]["A"].cell(2, 3) is None
        path.write_bytes(b"garbage-not-a-wk1")
        data = _persist.read_wk1(str(path))
        assert data["sheets"]["A"].cells == {}

    def test_wk1_write_read_roundtrip(self, tmp_path):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "123")
        ws.set_entry(1, 0, "'lbl")
        ws.set_entry(2, 0, "+A1*2")
        ws.set_entry(0, 1, "-2.5")
        ws.set_col_width(1, 20)
        ws.get_or_make(0, 1).fmt = {"type": "fixed", "decimals": 3}
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        path = str(tmp_path / "rt.wk1")
        _persist.write_wk1(path, ws, eng)
        data = _persist.read_wk1(path)
        ws2 = data["sheets"]["A"]
        assert ws2.value_at(0, 0) == 123.0
        assert ws2.value_at(1, 0) == "lbl"
        assert ws2.raw_at(2, 0).startswith("+")
        assert ws2.value_at(0, 1) == -2.5
        assert ws2.col_width(1) == 20
        assert ws2.cell(0, 1).fmt["type"] == "fixed"

    def test_wk3_crafted_two_sheets(self, tmp_path):
        num_a = _rec(0x0017, struct.pack("<HHH", 1, 2, 0) + struct.pack("<d", 9.5))
        lbl = _rec(0x0016, struct.pack("<HHH", 0, 0, 0) + b"'" + b"wk3\0")
        small = _rec(0x0018, struct.pack("<HHHh", 3, 0, 0, 0x0004))  # snum16 -> 2.0
        err = _rec(0x0014, struct.pack("<HHH", 4, 0, 0))
        na = _rec(0x0015, struct.pack("<HHH", 5, 0, 0))
        name = _rec(0x001B, struct.pack("<HH", 14000, 0) + b"DATA\0")
        userrange = _rec(0x0009, struct.pack("<H", 0) + b"TOTAL\0".ljust(16, b"\0")
                         + struct.pack("<HHHH", 0, 0, 2, 0))
        num_b = _rec(0x0017, struct.pack("<HHH", 0, 0, 1) + struct.pack("<d", 7.0))
        eof = _rec(0x0001, b"")
        path = tmp_path / "t.wk3"
        path.write_bytes(_wk3_bof() + num_a + lbl + small + err + na + name
                         + userrange + eof + _wk3_bof() + num_b + eof)
        data = _persist.read_wk3(str(path))
        assert set(data["sheets"]) == {"DATA", "B"}
        a = data["sheets"]["DATA"]
        assert a.value_at(1, 2) == 9.5
        assert a.value_at(0, 0) == "wk3"
        assert a.value_at(3, 0) == 2.0
        assert a.raw_at(4, 0) == "+@ERR()"
        assert a.raw_at(5, 0) == "+@NA()"
        assert data["sheets"]["B"].value_at(0, 0) == 7.0
        assert data["names"]["TOTAL"]["range"] == ((0, 0), (2, 0))
        assert data["names"]["TOTAL"]["sheet"] == "DATA"
        assert data["active"] == "DATA"

    def test_wk3_truncated_and_password(self, tmp_path):
        path = tmp_path / "trunc.wk3"
        path.write_bytes(_wk3_bof() + _rec(0x0017, b"\x01\x02")[:4])  # cut record
        data = _persist.read_wk3(str(path))
        assert list(data["sheets"]) == ["A"]     # sheet created, cell skipped
        p2 = tmp_path / "pw.wk3"
        p2.write_bytes(_wk3_bof() + _rec(0x0002, b"\x00\x00"))
        with pytest.raises(ValueError):
            _persist.read_wk3(str(p2))
        p3 = tmp_path / "empty.wk3"
        p3.write_bytes(b"")
        data = _persist.read_wk3(str(p3))
        assert data["sheets"] == {}

    def test_import_file_dispatch(self, tmp_path):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "5")
        pvg = tmp_path / "x.vg1"
        _persist.save_vg1(str(pvg), {"A": ws})
        assert _persist.import_file(str(pvg))["sheets"]["A"].value_at(0, 0) == 5.0
        pwk = tmp_path / "x.wk1"
        pwk.write_bytes(_rec(0x0000, struct.pack("<HH", 0x0404, 0))
                        + _rec(0x000E, struct.pack("<BHH", 0, 0, 0) + struct.pack("<d", 3.0))
                        + _rec(0x0001, b""))
        assert _persist.import_file(str(pwk))["sheets"]["A"].value_at(0, 0) == 3.0
        pwk3 = tmp_path / "x.wk3"
        pwk3.write_bytes(_wk3_bof() + _rec(0x0017, struct.pack("<HHH", 0, 0, 0)
                                            + struct.pack("<d", 8.0)) + _rec(0x0001, b""))
        assert _persist.import_file(str(pwk3))["sheets"]["A"].value_at(0, 0) == 8.0
        # sniff by content: no extension, WK3 BOF magic
        psniff = tmp_path / "noext"
        psniff.write_bytes(pwk3.read_bytes())
        assert _persist.import_file(str(psniff))["sheets"]["A"].value_at(0, 0) == 8.0
        ptxt = tmp_path / "noext2"
        ptxt.write_text("1,\"a b\",x\n2,3,4\n", encoding="utf-8")
        ws_t = _persist.import_file(str(ptxt))["sheets"]["A"]
        assert ws_t.value_at(0, 0) == 1.0
        assert ws_t.value_at(1, 0) == "a b"
        assert ws_t.value_at(2, 0) == "x"
        assert ws_t.value_at(1, 1) == 3.0

    def test_dif_roundtrip_and_edges(self, tmp_path):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "5")
        ws.set_entry(1, 0, "'name")
        ws.set_entry(0, 1, "2.5")
        ws.set_entry(1, 1, "'say \"hi\"")
        path = str(tmp_path / "t.dif")
        _persist.write_dif(path, ws)
        ws2 = _persist.read_dif(path)
        assert ws2.value_at(0, 0) == 5.0
        assert ws2.value_at(1, 0) == "name"
        assert ws2.value_at(0, 1) == 2.5
        assert ws2.value_at(1, 1) == 'say "hi"'
        # malformed DIF: no crash
        bad = tmp_path / "bad.dif"
        bad.write_text("TABLE\n0,1\nJUNK\nV\nnotanumber\n-1,0\nEOD\n", encoding="utf-8")
        ws3 = _persist.read_dif(str(bad))
        assert ws3.cell(0, 0) is None

    def test_text_import_edges(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text('"quoted, with comma",plain,42,"esc ""in"" here"\n'
                     ',-2.5,\n'
                     '\n'
                     '50%\n', encoding="utf-8")
        ws = _persist.read_text_import(str(p))
        assert ws.value_at(0, 0) == "quoted, with comma"
        assert ws.value_at(1, 0) == "plain"
        assert ws.value_at(2, 0) == 42.0
        assert ws.value_at(3, 0) == 'esc "in" here'
        assert ws.value_at(1, 1) == -2.5
        assert ws.value_at(0, 3) == 0.5     # 50% parses as number 0.5
        ws2 = _persist.read_text_import(str(p), numbers_only=True)
        assert ws2.value_at(0, 0) is None   # text dropped in numbers-only mode
        assert ws2.value_at(2, 0) == 42.0
        assert ws2.value_at(0, 3) == 0.5


# ---------------------------------------------------------------------------
# /DATA commands
# ---------------------------------------------------------------------------

class TestDataCmdBattery:
    def _mk(self, entries):
        from descartes.data_cmd import fill  # noqa
        ws = Worksheet("A")
        for addr, raw in entries.items():
            c, r = parse_address(addr)
            ws.set_entry(c, r, raw)
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        return ws, eng

    def test_fill_variants(self):
        from descartes.data_cmd import fill
        ws, eng = self._mk({})
        fill(ws, eng, split_range("A1..A5"), 10, -2, 4)
        assert [ws.value_at(0, r) for r in range(5)] == [10.0, 8.0, 6.0, 4.0, None]
        ws2, eng2 = self._mk({})
        fill(ws2, eng2, split_range("A1..C1"), 1, 10)      # horizontal, no stop
        assert [ws2.value_at(c, 0) for c in range(3)] == [1.0, 11.0, 21.0]
        ws3, eng3 = self._mk({})
        fill(ws3, eng3, split_range("A1..B2"), 1, 1)       # 2-D row-major
        assert [ws3.value_at(c, r) for r in range(2) for c in range(2)] == [1.0, 2.0, 3.0, 4.0]
        ws4, eng4 = self._mk({})
        fill(ws4, eng4, split_range("A1..A1"), 5, 1)       # single cell
        assert ws4.value_at(0, 0) == 5.0

    def test_sort_edges(self):
        from descartes.data_cmd import sort_range
        ws, eng = self._mk({})
        sort_range(ws, eng, split_range("A1..A3"), [(0, True)])   # empty range
        assert ws.cells == {}
        ws, eng = self._mk({"A1": "9"})
        sort_range(ws, eng, split_range("A1..A1"), [(0, True)])   # single cell
        assert ws.value_at(0, 0) == 9.0
        ws, eng = self._mk({"A1": "b", "A2": "2", "A3": "a", "A4": "1"})
        sort_range(ws, eng, split_range("A1..A4"), [(0, True)])
        assert [ws.value_at(0, r) for r in range(4)] == [1.0, 2.0, "a", "b"]
        # blanks in key column sink to the bottom
        ws, eng = self._mk({"A1": "x", "A3": "a"})      # A2 left blank
        sort_range(ws, eng, split_range("A1..A3"), [(0, True)])
        assert [ws.value_at(0, r) for r in range(3)] == ["a", "x", None]
        # two keys, mixed directions
        ws, eng = self._mk({"A1": "b", "B1": "2", "A2": "a", "B2": "1",
                            "A3": "b", "B3": "1"})
        sort_range(ws, eng, split_range("A1..B3"), [(0, True), (1, False)])
        assert [(ws.value_at(0, r), ws.value_at(1, r)) for r in range(3)] == \
            [("a", 1.0), ("b", 2.0), ("b", 1.0)]
        # formulas in the range move with their row and get remapped
        ws, eng = self._mk({"A1": "2", "B1": "+A1*10", "A2": "1", "B2": "+A2*10"})
        sort_range(ws, eng, split_range("A1..B2"), [(0, True)])
        assert ws.value_at(0, 0) == 1.0
        assert ws.raw_at(1, 0) == "+(A1*10)"     # remapped to the new row
        assert ws.value_at(1, 0) == 10.0
        assert ws.value_at(1, 1) == 20.0

    def _db(self):
        return self._mk({
            "A1": "Name", "B1": "Age", "C1": "City",
            "A2": "ann", "B2": "30", "C2": "NYC",
            "A3": "Bob", "B3": "25", "C3": "LA",
            "A4": "cyd", "B4": "40", "C4": "NYC",
        })

    def test_query_operators(self):
        from descartes.data_cmd import query
        ws, eng = self._db()
        ws.set_entry(4, 0, "Age"); ws.set_entry(4, 1, ">=30")
        m = query(ws, eng, split_range("A1..C4"), split_range("E1..E2"))
        assert m == [1, 3]
        ws.set_entry(4, 1, "<>30")
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [2, 3]
        ws.set_entry(4, 1, "<26")
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [2]
        ws.set_entry(4, 0, "City"); ws.set_entry(4, 1, "=NYC")
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [1, 3]
        ws.set_entry(4, 1, "=nyc")          # case-insensitive
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [1, 3]
        ws.set_entry(4, 1, "N*")            # wildcard
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [1, 3]
        ws.set_entry(4, 1, "L?")
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == [2]
        ws.set_entry(4, 1, "Z*")            # no match
        assert query(ws, eng, split_range("A1..C4"), split_range("E1..E2")) == []

    def test_query_or_unique_delete_extract(self):
        from descartes.data_cmd import query
        ws, eng = self._db()
        ws.set_entry(4, 0, "Age")
        ws.set_entry(4, 1, ">=30")          # OR row 1
        ws.set_entry(4, 2, "<26")           # OR row 2
        m = query(ws, eng, split_range("A1..C4"), split_range("E1..E3"))
        assert m == [1, 2, 3]
        # formula criterion, written against first data row
        ws2, eng2 = self._db()
        ws2.set_entry(4, 0, "Age")
        ws2.set_entry(4, 1, "+B2>30")
        assert query(ws2, eng2, split_range("A1..C4"), split_range("E1..E2")) == [3]
        # delete removes matched rows
        ws3, eng3 = self._db()
        ws3.set_entry(4, 0, "City"); ws3.set_entry(4, 1, "NYC")
        query(ws3, eng3, split_range("A1..C4"), split_range("E1..E2"), action="delete")
        assert ws3.value_at(0, 1) is None and ws3.value_at(0, 3) is None
        assert ws3.value_at(0, 2) == "Bob"
        # extract copies header + matches to output range
        ws4, eng4 = self._db()
        ws4.set_entry(4, 0, "City"); ws4.set_entry(4, 1, "NYC")
        m = query(ws4, eng4, split_range("A1..C4"), split_range("E1..E2"),
                  output_rng=split_range("G1..I3"), action="extract")
        assert m == [1, 3]
        assert ws4.value_at(6, 0) == "Name"
        assert ws4.value_at(6, 1) == "ann"
        assert ws4.value_at(6, 2) == "cyd"
        # unique dedupes identical rows
        ws5, eng5 = self._mk({
            "A1": "K", "A2": "x", "A3": "x", "A4": "y",
            "E1": "K", "E2": "x",
        })
        m = query(ws5, eng5, split_range("A1..A4"), split_range("E1..E2"),
                  output_rng=split_range("G1..G3"), unique=True, action="extract")
        assert m == [1, 2]
        assert ws5.value_at(6, 1) == "x"
        assert ws5.cell(6, 2) is None

    def test_frequency_edges(self):
        from descartes.data_cmd import frequency
        ws, eng = self._mk({"A1": "1", "A2": "2", "A3": "3", "A4": "4", "A5": "5",
                            "A6": "'skipme",
                            "D1": "2", "D2": "4"})
        counts = frequency(ws, eng, split_range("A1..A6"), split_range("D1..D2"))
        assert counts == [2, 2, 1]
        assert ws.value_at(4, 0) == 2.0     # counts in col E (right of bins)
        assert ws.value_at(4, 1) == 2.0
        assert ws.value_at(4, 2) == 1.0
        # unsorted bins: first bin with v <= bound wins
        ws2, eng2 = self._mk({"A1": "1", "A2": "5", "D1": "4", "D2": "2"})
        counts = frequency(ws2, eng2, split_range("A1..A2"), split_range("D1..D2"))
        assert counts == [1, 0, 1]
        # horizontal bins: counts in the row below
        ws3, eng3 = self._mk({"A1": "1", "A2": "9", "C1": "5", "D1": "10"})
        counts = frequency(ws3, eng3, split_range("A1..A2"), split_range("C1..D1"))
        assert counts == [1, 1, 0]
        assert ws3.value_at(2, 1) == 1.0
        assert ws3.value_at(3, 1) == 1.0
        assert ws3.value_at(4, 1) == 0.0
        # empty data
        ws4, eng4 = self._mk({"D1": "5"})
        assert frequency(ws4, eng4, split_range("A1..A3"), split_range("D1..D1")) == [0, 0]

    def test_regression_edges(self):
        from descartes.data_cmd import regression
        ws, eng = self._mk({"A1": "1", "A2": "2", "A3": "3",
                            "B1": "2", "B2": "4", "B3": "6"})
        regression(ws, eng, split_range("A1..A3"), split_range("B1..B3"), (0, 5))
        assert ws.value_at(1, 5) == 2.0     # slope
        assert ws.value_at(1, 6) == 0.0     # intercept
        assert ws.value_at(1, 7) == 1.0     # r^2
        assert ws.value_at(1, 10) == 3.0    # n
        assert ws.value_at(0, 5) == "Slope"
        ws2, eng2 = self._mk({"A1": "1", "B1": "2"})
        with pytest.raises(ValueError):
            regression(ws2, eng2, split_range("A1..A1"), split_range("B1..B1"), (0, 5))
        ws3, eng3 = self._mk({"A1": "5", "A2": "5", "B1": "1", "B2": "2"})
        with pytest.raises(ValueError):
            regression(ws3, eng3, split_range("A1..A2"), split_range("B1..B2"), (0, 5))

    def test_tables(self):
        from descartes.data_cmd import data_table_1var, data_table_2var
        ws, eng = self._mk({"A1": "0", "B1": "+A1*2"})
        data_table_1var(ws, eng, (0, 0), (1, 0), split_range("A3..A6"), [10, 20, 30])
        assert ws.value_at(1, 3) == 20.0
        assert ws.value_at(1, 4) == 40.0
        assert ws.value_at(1, 5) == 60.0
        ws2, eng2 = self._mk({"A1": "0", "B1": "0", "C1": "+A1+B1",
                              "A4": "10", "A5": "20", "B3": "1", "C3": "2"})
        data_table_2var(ws2, eng2, (0, 0), (1, 0), (2, 0), split_range("A3..C5"))
        assert ws2.value_at(1, 3) == 11.0
        assert ws2.value_at(1, 4) == 21.0
        assert ws2.value_at(2, 3) == 12.0
        assert ws2.value_at(2, 4) == 22.0


# ---------------------------------------------------------------------------
# raw terminal input (keys.py RawReader / MouseParser)
# ---------------------------------------------------------------------------

class TestRawReaderBattery:
    def _reader(self):
        import os
        from descartes.keys import RawReader
        r, w = os.pipe()
        return RawReader(r, escdelay_ms=5), r, w

    def test_plain_chars_and_controls(self):
        rd, r, w = self._reader()
        import os
        os.write(w, b"a\r\t\x7f")
        evs = [rd.read_event() for _ in range(4)]
        assert evs[0].kind == "char" and evs[0].ch == "a"
        assert evs[1].kind == "key" and evs[1].name == "ENTER"
        assert evs[2].kind == "key" and evs[2].name == "TAB"
        assert evs[3].kind == "key" and evs[3].name == "BACKSPACE"
        os.close(w); os.close(r)

    def test_utf8_multibyte(self):
        import os
        rd, r, w = self._reader()
        os.write(w, "é€".encode("utf-8"))
        ev1 = rd.read_event()
        ev2 = rd.read_event()
        assert ev1.ch == "é" and ev2.ch == "€"
        # split across two writes before reading: buffered into one char
        os.write(w, "€".encode("utf-8")[:1])
        os.write(w, "€".encode("utf-8")[1:])
        assert rd.read_event().ch == "€"
        os.close(w); os.close(r)

    def test_escape_sequences(self):
        import os
        rd, r, w = self._reader()
        os.write(w, b"\x1b[A\x1b[1;5C\x1bOP\x1b[5~\x1b[99z" + b"q")
        names = []
        chs = []
        while True:
            ev = rd.read_event()
            if ev is None:
                break
            if ev.kind == "key":
                names.append(ev.name)
            elif ev.kind == "char":
                chs.append(ev.ch)
            if chs:
                break
        assert names == ["UP", "CTRL_RIGHT", "F1", "PGUP"]
        assert chs == ["q"]      # unknown CSI 99z silently consumed
        os.close(w); os.close(r)

    def test_lone_esc_and_alt(self):
        import os
        rd, r, w = self._reader()
        os.write(w, b"\x1b")          # nothing after ESC
        ev = rd.read_event()
        assert ev.kind == "key" and ev.name == "ESC"
        os.write(w, b"\x1bx")
        ev = rd.read_event()
        assert ev.kind == "char" and ev.ch == "x" and ev.name == "ALT+x"
        os.close(w); os.close(r)

    def test_sgr_and_legacy_mouse(self):
        import os
        rd, r, w = self._reader()
        os.write(w, b"\x1b[<0;10;5M")       # press button0 at (9,4)
        m = rd.read_event()
        assert m.kind == "mouse" and m.mouse.button == 0 and m.mouse.press
        assert (m.mouse.x, m.mouse.y) == (9, 4)
        os.write(w, b"\x1b[<0;10;5m")       # release
        m = rd.read_event()
        assert m.mouse.release and not m.mouse.press
        os.write(w, b"\x1b[<64;10;5M")      # wheel up
        m = rd.read_event()
        assert m.mouse.wheel_up
        os.write(w, b"\x1b[<65;10;5M")      # wheel down
        assert rd.read_event().mouse.wheel_down
        os.write(w, b"\x1b[<17;10;5M")      # ctrl+click (cb 16+1)
        m = rd.read_event()
        assert m.mouse.button == 1 and "ctrl" in m.mouse.modifiers
        os.write(w, b"\x1b[M" + bytes([32 + 0, 32 + 10, 32 + 5]))   # legacy press
        m = rd.read_event()
        assert m.kind == "mouse" and (m.mouse.x, m.mouse.y) == (9, 4) and m.mouse.press
        os.close(w); os.close(r)

    def test_mouse_parser_incremental(self):
        from descartes.keys import MouseParser
        mp = MouseParser()
        evs, junk = mp.feed(b"\x1b[<0;10;")       # partial SGR held in buffer
        assert evs == []
        evs, junk = mp.feed(b"5M")
        assert len(evs) == 1 and evs[0].x == 9
        evs, junk = mp.feed(b"\x1b[M")             # partial legacy held
        assert evs == []
        evs, junk = mp.feed(bytes([32, 42, 65]))   # Cb=0, Cx=10, Cy=33 -> (9,32)
        assert len(evs) == 1 and evs[0].y == 32 and evs[0].x == 9

    def test_eof(self):
        import os
        rd, r, w = self._reader()
        os.write(w, b"a")
        assert rd.read_event().ch == "a"
        os.close(w)
        assert rd.read_event() is None
        assert rd.eof
        os.close(r)

    def test_decode_code(self, fake_curses):
        from descartes.keys import decode_code
        assert decode_code(27) == "ESC"
        assert decode_code(9) == "TAB"
        assert decode_code(13) == "ENTER"
        assert decode_code(3) is None       # ctrl char -> char event
        assert decode_code(65) is None      # printable
        assert decode_code(0xFFFF + 1) == "KEY65536"


# ---------------------------------------------------------------------------
# app interaction (fake curses)
# ---------------------------------------------------------------------------

from descartes.keys import KeyEvent, MouseEvent  # noqa: E402


class TestAppBattery:
    def test_entry_modes_and_esc(self, make_app):
        app, win = make_app()
        type_text(app, "123"); press(app, "ENTER")
        assert app.sheet.value_at(0, 0) == 123.0
        type_text(app, "'lbl"); press(app, "ENTER")
        assert app.sheet.value_at(0, 1) == "lbl"
        type_text(app, "+A1*2"); press(app, "ENTER")
        assert app.sheet.value_at(0, 2) == 246.0
        # ESC aborts an in-progress entry
        type_text(app, "999")
        press(app, "ESC")
        assert app.mode == "READY"
        assert app.sheet.cell(0, 3) is None
        # bad formula -> error line, cell unchanged
        type_text(app, "+1+"); press(app, "ENTER")
        assert "ERROR" in app.error
        assert app.sheet.cell(0, 3) is None

    def test_edit_and_protection(self, make_app):
        app, win = make_app()
        type_text(app, "99"); press(app, "ENTER")
        press(app, "UP")
        press(app, "F2")
        assert app.mode == "EDIT"
        press(app, "ESC")
        assert app.mode == "READY"
        # protection: enable sheet protection, protect A1, then edits are blocked
        app.sheet.protection = True
        app.cmd_range_protect(split_range("A1..A1"), True)
        press(app, "F2")                      # cursor still on A1
        assert "Protected" in app.error
        press(app, "ESC")                     # dismiss ERROR mode
        assert app.mode == "READY"
        type_text(app, "5"); press(app, "ENTER")
        assert app.sheet.value_at(0, 0) == 99.0   # unchanged
        assert "Protected" in app.error
        app.sheet.protection = False

    def test_menu_navigation_and_dialog_validation(self, make_app):
        app, win = make_app()
        char(app, "/")
        assert app.mode == "MENU"
        press(app, "RIGHT"); press(app, "RIGHT")   # -> Copy
        press(app, "ENTER")
        assert app.mode == "DIALOG"
        type_text(app, "!!!"); press(app, "ENTER")  # invalid range
        assert app.error != ""
        char(app, "\x03")
        assert app.mode == "READY"
        assert app.message == "CANCELED"
        # ESC closes an open menu without side effects
        char(app, "/")
        assert app.mode == "MENU"
        press(app, "ESC")
        assert app.mode == "READY"

    def test_point_mode_and_esc(self, make_app):
        app, win = make_app()
        type_text(app, "7"); press(app, "ENTER")
        char(app, "/")
        press(app, "RIGHT"); press(app, "RIGHT"); press(app, "ENTER")  # COPY
        press(app, "ENTER")                    # empty -> POINT for range
        assert app.mode == "POINT"
        char(app, "\x03")
        assert app.mode == "READY"
        assert app.message == "CANCELED"

    def test_sheet_switch_wrap(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()                 # adds sheet B, makes it active
        assert app.active == "B"
        press(app, "CTRL_PGDN")
        assert app.active == "A"
        press(app, "CTRL_PGDN")                # wraps around
        assert app.active == "B"
        press(app, "CTRL_PGUP")
        assert app.active == "A"

    def test_insert_col_adjusts_formulas(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(1, 0, "+A1*2")
        app._register_all(); app._recalc(force=True)
        assert app.sheet.value_at(1, 0) == 10.0
        app.cmd_insert_col(0)                  # everything shifts right one
        assert app.sheet.value_at(1, 0) == 5.0         # A1 -> B1
        assert app.sheet.value_at(2, 0) == 10.0        # formula -> C1, refs adjusted
        assert "B1" in app.sheet.raw_at(2, 0)

    def test_delete_row_marks_refs_err(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(1, 1, "+A1*10")            # B2 -> 10
        app._register_all(); app._recalc(force=True)
        assert app.sheet.value_at(1, 1) == 10.0
        app.cmd_delete_row(0)                          # delete row 1
        assert app.sheet.value_at(0, 0) is None        # A1 deleted
        # B2 moved up to B1; its ref A1 was in the deleted band -> @ERR()
        assert is_err(app.sheet.value_at(1, 0))

    def test_range_commands(self, make_app):
        app, win = make_app()
        type_text(app, "1"); press(app, "ENTER")
        type_text(app, "2"); press(app, "ENTER")
        app.cmd_range_format(split_range("A1..A2"), "FIXED")
        assert app.sheet.cell(0, 0).fmt["type"] == "fixed"
        app.cmd_range_label(split_range("A1..A1"), "CENTER")
        app.cmd_range_erase(split_range("A2..A2"))
        assert app.sheet.cell(0, 1) is None
        app.cmd_global_format("PERCENT")
        assert app.sheet.global_fmt["type"] == "percent"

    def test_justify_rotates_rows(self, make_app):
        app, win = make_app()
        for i, ch in enumerate("abc"):
            app.sheet.set_entry(i, 0, "'" + ch)
        app._register_all()
        app.cmd_range_justify(split_range("A1..C1"))
        assert app.sheet.value_at(0, 0) == "c"
        assert app.sheet.value_at(1, 0) == "a"
        assert app.sheet.value_at(2, 0) == "b"

    def test_backsolve(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "2")
        app.sheet.set_entry(1, 0, "+A1*10")
        app._register_all(); app._recalc(force=True)
        app.cmd_backsolve("B1", "100", "A1")
        assert app.sheet.value_at(0, 0) == 10.0
        assert "Backsolved" in app.message
        app.cmd_backsolve("B1", "abc", "A1")
        assert "not a number" in app.error
        # non-numeric target
        app2, _ = make_app()
        app2.sheet.set_entry(0, 0, "'x")
        app2.sheet.set_entry(1, 0, "+1")
        app2._register_all(); app2._recalc(force=True)
        app2.cmd_backsolve("A1", "5", "B1")
        assert "not numeric" in app2.error

    def test_transpose_and_range_value(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(1, 0, "2")
        app.sheet.set_entry(0, 1, "3")
        app._register_all(); app._recalc(force=True)
        app.cmd_transpose(split_range("A1..B2"), (0, 5))
        assert app.sheet.value_at(0, 5) == 1.0
        assert app.sheet.value_at(1, 5) == 3.0
        assert app.sheet.value_at(0, 6) == 2.0
        app.cmd_range_value(split_range("A1..B2"), (0, 10))
        assert app.sheet.kind_at(0, 10) == NUMBER
        assert app.sheet.value_at(0, 10) == 1.0
        assert app.sheet.value_at(1, 10) == 2.0
        assert app.sheet.value_at(0, 11) == 3.0

    def test_search(self, make_app):
        app, win = make_app()
        type_text(app, "'needle here"); press(app, "ENTER")
        type_text(app, "'hay"); press(app, "ENTER")
        app.cmd_search(split_range("A1..C5"), "NEEDLE")
        assert app.cur == (0, 0)
        assert "Found 1" in app.message
        app.cmd_search(split_range("A1..C5"), "zzz")
        assert "Not found" in app.message

    def test_copy_3d_to_2d_rejected(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()
        type_text(app, "5"); press(app, "ENTER")
        app.cmd_copy(["A:B!A1..A1", "C1"], False)
        assert app.error != ""
        assert app.sheet.cell(2, 0) is None

    def test_save_load_roundtrip(self, make_app, tmp_path):
        app, win = make_app()
        type_text(app, "42"); press(app, "ENTER")
        type_text(app, "+A1*2"); press(app, "ENTER")
        type_text(app, "'tag"); press(app, "ENTER")
        path = str(tmp_path / "rt")
        app.cmd_save(path)
        app2, win2 = make_app(filepath=path + ".vg1")
        assert app2.sheet.value_at(0, 0) == 42.0
        assert app2.sheet.value_at(0, 1) == 84.0
        assert app2.sheet.value_at(0, 2) == "tag"

    def test_quit(self, make_app):
        app, win = make_app()
        char(app, "\x11")
        assert app.running is False

    def test_system_and_help_overlays(self, make_app):
        app, win = make_app()
        app.cmd_system("echo hello-123")
        assert app.mode == "SYSTEM"
        snap = screen_text(app, win)
        assert "SYSTEM" in snap and "hello-123" in snap
        press(app, "x")
        assert app.mode == "READY"
        press(app, "F1")
        assert app.mode == "HELP"
        snap = screen_text(app, win)
        assert "HELP" in snap
        press(app, "x")
        assert app.mode == "READY"

    def test_graph_view_overlay(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "2")
        app._register_all(); app._recalc(force=True)
        app.cmd_graph_series("A1..A2", "S")
        app.cmd_graph_view()
        assert app.mode == "GRAPH"
        snap = screen_text(app, win)
        assert "GRAPH VIEW" in snap
        press(app, "x")
        assert app.mode == "READY"

    def test_mouse_drag_select(self, make_app):
        app, win = make_app()
        from descartes.keys import MouseEvent
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=8, y=4, button=0, press=True)))
        assert app.cur == (0, 0)
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=8 + 9, y=4 + 2, button=0,
                                                       press=True, drag=True)))
        assert app.sel_range is not None
        (c1, r1), (c2, r2) = app.sel_range
        assert (c1, r1) == (0, 0) and (c2, r2) == (1, 2)

    def test_mouse_column_resize(self, make_app):
        app, win = make_app()
        from descartes.keys import MouseEvent
        w0 = app.sheet.col_width(0)
        bx = app.col_x(0) + w0 - 1          # right boundary of column A header
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=bx, y=3, button=0, press=True)))
        assert app._resize_col is not None
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=bx + 5, y=3, button=0,
                                                       press=True, drag=True)))
        assert app.sheet.col_width(0) == w0 + 5
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=bx + 5, y=3, button=0,
                                                       release=True)))
        assert app._resize_col is None
        # double-click header autofits
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=app.col_x(0) + 2, y=3,
                                                       button=0, press=True)))
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=app.col_x(0) + 2, y=3,
                                                       button=0, press=True)))
        assert app._resize_col is None

    def test_status_and_mem_indicator(self, make_app, monkeypatch):
        app, win = make_app()
        app.cmd_status()
        assert "cells=" in app.message and "A1" in app.message
        snap = screen_text(app, win)
        assert "INS" in snap.split("\n")[22]
        monkeypatch.setattr(app, "mem_bytes", lambda: 600 * 1024)
        snap = screen_text(app, win)
        assert "MEM" in snap.split("\n")[22]
        app.overwrite = True
        snap = screen_text(app, win)
        assert "OVR" in snap.split("\n")[22]

    def test_print_3d_to_file(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.cmd_insert_sheet()               # active sheet becomes B
        app.sheet.set_entry(0, 0, "2")
        app._register_all(); app._recalc(force=True)
        out = str(tmp_path / "out.txt")
        app.cmd_print("A:B!A1..A1", "file:" + out)
        text = open(out, encoding="utf-8").read()
        assert "Sheet A" in text and "Sheet B" in text
        assert "1" in text and "2" in text

    def test_range_input(self, make_app):
        app, win = make_app()
        app.cmd_range_input(split_range("B2..C3"))
        assert app.input_range is not None
        assert app.input_range[0] == (1, 1)


# ---------------------------------------------------------------------------
# macros
# ---------------------------------------------------------------------------

class TestMacroBattery:
    def _app_with(self, make_app, macros):
        app, win = make_app()
        from descartes import macro as _m
        for addr, raw in macros.items():
            c, r = parse_address(addr)
            app.sheet.set_entry(c, r, raw)
        app.engine.register_all()
        app.engine.recalc(force=True)
        return app, _m.MacroRunner(app)

    def test_let_goto_keys(self, make_app):
        app, run = self._app_with(make_app, {"A1": "{LET B1, 5}{GOTO C3}"})
        run.run_cell((0, 0))
        assert app.sheet.value_at(1, 0) == 5.0
        assert app.cur == (2, 2)
        app2, run2 = self._app_with(make_app, {"A1": "{GOTO A2}5{ENTER}"})
        run2.run_cell((0, 0))
        assert app2.sheet.value_at(0, 1) == 5.0

    def test_for_loop_and_break(self, make_app):
        from descartes import macro as _m
        app, run = self._app_with(make_app, {"A1": "{FOR A1,1,3,1}{LET B1, 7}{FOREND}"})
        run.run_cell((0, 0))
        assert app.sheet.value_at(0, 0) == 3.0
        assert app.sheet.value_at(1, 0) == 7.0
        app2, run2 = self._app_with(make_app,
                                     {"A1": "{FOR A1,1,10,1}{IF +A1>2}{FORBREAK}{END}{LET B1, 1}{FOREND}"})
        run2.run_cell((0, 0))
        assert app2.sheet.value_at(0, 0) == 3.0
        # step 0 -> error
        app3, run3 = self._app_with(make_app, {"A1": "{FOR A1,1,5,0}{FOREND}"})
        with pytest.raises(_m.MacroError):
            run3.run_cell((0, 0))
        # runaway -> step limit
        app4 = make_app()[0]
        app4.sheet.set_entry(0, 0, "{FOR A1,1,100000,1}{BEEP}{FOREND}")
        run4 = _m.MacroRunner(app4, max_steps=100)
        with pytest.raises(_m.MacroError):
            run4.run_cell((0, 0))

    def test_if_else_nested(self, make_app):
        app, run = self._app_with(make_app, {
            "A1": "{IF +A2>1}{LET B1, 1}{ELSE}{LET B1, 2}{END}",
            "A2": "5"})
        run.run_cell((0, 0))
        assert app.sheet.value_at(1, 0) == 1.0
        app.sheet.set_entry(0, 1, "0")
        app.engine.recalc(force=True)
        from descartes import macro as _m
        _m.MacroRunner(app).run_cell((0, 0))
        assert app.sheet.value_at(1, 0) == 2.0

    def test_call_depth_and_branch(self, make_app):
        from descartes import macro as _m
        app, run = self._app_with(make_app, {"A1": "{CALL B1}", "B1": "{CALL A1}"})
        with pytest.raises(_m.MacroError):
            run.run_cell((0, 0))
        app2, run2 = self._app_with(make_app, {"A1": "{BRANCH B1}", "B1": "5"})
        with pytest.raises(_m.MacroError):
            run2.run_cell((0, 0))
        app3, run3 = self._app_with(make_app, {"A1": "{BRANCH B1}", "B1": "{LET C1, 8}"})
        run3.run_cell((0, 0))
        assert app3.sheet.value_at(2, 0) == 8.0

    def test_unknown_command_and_wait(self, make_app):
        from descartes import macro as _m
        app, run = self._app_with(make_app, {"A1": "{FROB 1}"})
        with pytest.raises(_m.MacroError):
            run.run_cell((0, 0))
        app2, run2 = self._app_with(make_app, {"A1": "{WAIT abc}{LET B1, 1}"})
        run2.run_cell((0, 0))            # bad WAIT arg ignored, no crash
        assert app2.sheet.value_at(1, 0) == 1.0

    def test_input_requires_range_then_collects(self, make_app):
        from descartes import macro as _m
        app, run = self._app_with(make_app, {"A1": "{INPUT}"})
        with pytest.raises(_m.MacroError):
            run.run_cell((0, 0))
        app2, run2 = self._app_with(make_app, {"A3": "{INPUT}{LET B1, 9}"})
        app2.cmd_range_input(split_range("A1..A2"))
        run2.run_cell((0, 2))
        assert app2.macro_wait is not None
        assert app2.macro_wait["kind"] == "input"
        type_text(app2, "42")
        press(app2, "ENTER")
        assert app2.sheet.value_at(0, 0) == 42.0
        assert app2.sheet.value_at(1, 0) == 9.0
        assert app2.macro_wait is None

    def test_getnumber_suspend_resume(self, make_app):
        from descartes import macro as _m
        app, run = self._app_with(make_app, {"A1": '{GETNUMBER "val?", B1}{LET C1, 3}'})
        run.run_cell((0, 0))
        assert app.macro_wait is not None and app.macro_wait["kind"] == "number"
        app._finish_macro_wait(["12.5"])
        assert app.sheet.value_at(1, 0) == 12.5
        assert app.sheet.value_at(2, 0) == 3.0

    def test_quit_and_system(self, make_app):
        app, run = self._app_with(make_app, {"A1": "{QUIT}"})
        run.run_cell((0, 0))
        assert app.running is False
        app2, run2 = self._app_with(make_app, {"A1": "{SYSTEM echo mk-99}"})
        run2.run_cell((0, 0))
        assert app2.mode == "SYSTEM"
        assert "mk-99" in app2.system_lines

    def test_alt_letter_macro(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "{LET B1, 77}")
        app.cmd_name_create("M", "A1")
        app.handle(KeyEvent("char", ch="m", name="ALT+m"))
        assert app.sheet.value_at(1, 0) == 77.0


# ---------------------------------------------------------------------------
# screen rendering
# ---------------------------------------------------------------------------

class TestScreenBattery:
    def test_menubar_and_menu_draw(self, make_app):
        app, win = make_app()
        snap = screen_text(app, win)
        assert snap.split("\n")[0].strip() == ""        # blank bar in READY
        char(app, "/")
        snap = screen_text(app, win)
        assert "WORKSHEET" in snap.split("\n")[0]
        press(app, "ENTER")
        snap = screen_text(app, win)
        assert "INSERT" in snap                          # WORKSHEET dropdown visible

    def test_message_and_error_lines(self, make_app):
        app, win = make_app()
        app.message_msg("hello-msg")
        app.error_msg("boom-err")
        snap = screen_text(app, win)
        assert "hello-msg" in snap
        assert "boom-err" in snap
        press(app, "x")                     # ERROR mode dismiss
        assert app.error == ""

    def test_overlay_box(self, make_app):
        app, win = make_app()
        draw(app)
        app.screen.overlay_box("TITLE", ["alpha", "beta"])
        snap = win.snapshot()
        assert "TITLE" in snap and "alpha" in snap and "beta" in snap

    def test_cell_content_rendered(self, make_app):
        app, win = make_app()
        type_text(app, "42"); press(app, "ENTER")
        press(app, "UP")
        snap = screen_text(app, win)
        assert "42" in snap
        # control panel shows the cell address and mode
        assert "A1" in snap and "READY" in snap
