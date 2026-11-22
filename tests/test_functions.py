import math

import pytest

from descartes.engine import Engine
from descartes.functions import ERR, NA, Error, FUNCS
from descartes.grid import Worksheet, parse_address


def ev(formula):
    ws = Worksheet()
    ws.set_entry(0, 0, formula)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws.value_at(0, 0)


def setup(entries):
    ws = Worksheet()
    for addr, raw in entries.items():
        c, r = parse_address(addr)
        ws.set_entry(c, r, raw)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws


def val(ws, addr):
    c, r = parse_address(addr)
    return ws.value_at(c, r)


def test_registry_complete():
    expected = """SUM AVG COUNT MAX MIN STD VAR SQRT ROUND INT ABS EXP LN LOG PI SIN COS TAN
    ASIN ACOS ATAN MOD RAND IF HLOOKUP VLOOKUP INDEX CHOOSE ISERR ISNA ISNUMBER ISSTRING
    ERR NA LENGTH STRING VALUE LEFT RIGHT MID UPPER LOWER TRIM REPEAT CHAR CODE DATE
    DATEVALUE NOW TIME YEAR MONTH DAY TODAY FV PV NPV IRR TERM RATE PAYMENT""".split()
    missing = [n for n in expected if n not in FUNCS]
    assert not missing, f"missing @functions: {missing}"


class TestNumeric:
    def test_stats(self):
        ws = setup({"A1": "1", "A2": "2", "A3": "3", "B1": "@SUM(A1..A3)",
                    "B2": "@AVG(A1..A3)", "B3": "@COUNT(A1..A3)", "B4": "@MAX(A1..A3)",
                    "B5": "@MIN(A1..A3)", "B6": "@STD(A1..A3)", "B7": "@VAR(A1..A3)"})
        assert val(ws, "B1") == 6.0
        assert val(ws, "B2") == 2.0
        assert val(ws, "B3") == 3.0
        assert val(ws, "B4") == 3.0
        assert val(ws, "B5") == 1.0
        assert val(ws, "B6") == pytest.approx(1.0)
        assert val(ws, "B7") == pytest.approx(1.0)

    def test_math(self):
        assert ev("@SQRT(16)") == 4.0
        assert ev("@ROUND(3.567,2)") == 3.57
        assert ev("@INT(-2.5)") == -3.0
        assert ev("@ABS(-3)") == 3.0
        assert ev("@EXP(0)") == 1.0
        assert ev("@LN(1)") == 0.0
        assert ev("@LOG(100)") == 2.0
        assert ev("@LOG(8,2)") == 3.0
        assert ev("@PI()") == pytest.approx(math.pi)
        assert ev("@SIN(0)") == 0.0
        assert ev("@COS(0)") == 1.0
        assert ev("@TAN(0)") == 0.0
        assert ev("@ASIN(1)") == pytest.approx(math.pi / 2)
        assert ev("@ACOS(1)") == 0.0
        assert ev("@ATAN(1)") == pytest.approx(math.pi / 4)
        assert ev("@MOD(7,3)") == 1.0
        assert 0 <= ev("@RAND") < 1
        assert isinstance(ev("@SQRT(-1)"), Error)
        assert isinstance(ev("@LN(0)"), Error)


class TestLogic:
    def test_if(self):
        assert ev("@IF(1,10,20)") == 10.0
        assert ev("@IF(0,10,20)") == 20.0
        assert ev("@IF(2>1,\"yes\",\"no\")") == "yes"

    def test_is_functions(self):
        assert ev("@ISERR(1/0)") == 1.0
        assert ev("@ISERR(1)") == 0.0
        assert ev("@ISNA(@NA)") == 1.0
        assert ev("@ISNUMBER(5)") == 1.0
        assert ev("@ISSTRING(\"x\")") == 1.0
        assert isinstance(ev("@ERR"), Error)
        assert ev("@NA").code == "NA"

    def test_choose(self):
        assert ev("@CHOOSE(2,\"a\",\"b\",\"c\")") == "b"

    def test_lookup(self):
        ws = setup({"A1": "1", "B1": "10", "C1": "100",
                    "A2": "2", "B2": "20", "C2": "200",
                    "E1": "@VLOOKUP(2,A1..C2,3)", "E2": "@HLOOKUP(10,A1..C2,2)",
                    "E3": "@INDEX(A1..C2,2,3)"})
        assert val(ws, "E1") == 200.0
        assert val(ws, "E2") == 20.0
        assert val(ws, "E3") == 200.0


class TestString:
    def test_string_funcs(self):
        assert ev('@LENGTH("hello")') == 5.0
        assert ev('@STRING(3.14159,2)') == "3.14"
        assert ev('@VALUE("42")') == 42.0
        assert ev('@LEFT("hello",2)') == "he"
        assert ev('@RIGHT("hello",2)') == "lo"
        assert ev('@MID("hello",2,3)') == "ell"
        assert ev('@UPPER("abc")') == "ABC"
        assert ev('@LOWER("ABC")') == "abc"
        assert ev('@TRIM("  a   b ")') == "a b"
        assert ev('@REPEAT("ab",3)') == "ababab"
        assert ev("@CHAR(65)") == "A"
        assert ev('@CODE("A")') == 65.0


class TestDate:
    def test_date_serial(self):
        # 1900-01-01 = serial 1 (Lotus epoch)
        assert ev("@DATE(1900,1,1)") == 1.0
        # 1900-03-01 = serial 61 (includes fictitious 1900-02-29)
        assert ev("@DATE(1900,3,1)") == 61.0
        assert ev("@DATE(2024,1,1)") == ev("@DATEVALUE(\"01/01/2024\")")
        assert ev("@YEAR(@DATE(2024,6,15))") == 2024.0
        assert ev("@MONTH(@DATE(2024,6,15))") == 6.0
        assert ev("@DAY(@DATE(2024,6,15))") == 15.0
        assert ev("@TIME(12,0,0)") == 0.5
        assert ev("@TODAY()") > 40000
        assert ev("@NOW()") > 40000


class TestFinancial:
    def test_pv_fv_payment(self):
        # PV of 100 per period for 10 periods at 10% = 614.46
        assert ev("@PV(0.1,10,-100)") == pytest.approx(614.457, rel=1e-3)
        assert ev("@FV(0.1,10,-100)") == pytest.approx(1593.74, rel=1e-3)
        pmt = ev("@PAYMENT(0.1,10,1000)")
        assert pmt == pytest.approx(-162.745, rel=1e-3)

    def test_npv(self):
        ws = setup({"A1": "100", "A2": "200", "A3": "300", "B1": "@NPV(0.1,A1..A3)"})
        assert val(ws, "B1") == pytest.approx(100 / 1.1 + 200 / 1.21 + 300 / 1.331, rel=1e-6)

    def test_irr(self):
        ws = setup({"A1": "-1000", "A2": "400", "A3": "400", "A4": "400",
                    "B1": "@IRR(A1..A4)"})
        assert val(ws, "B1") == pytest.approx(0.0975, rel=1e-2)

    def test_rate_term(self):
        r = ev("@RATE(10,-100,614.457)")
        assert r == pytest.approx(0.1, rel=1e-3)
        assert ev("@TERM(0.1,1000,2000)") == pytest.approx(math.log(2) / math.log(1.1), rel=1e-6)
