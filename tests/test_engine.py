import time

import pytest

from descartes.engine import (Engine, FormulaSyntaxError, collect_refs, parse,
                               serialize, shift_raw)
from descartes.functions import ERR, NA, Error
from descartes.grid import Worksheet


def make_ws(entries):
    ws = Worksheet()
    for (c, r), raw in entries.items():
        ws.set_entry(c, r, raw)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws, eng


def val(ws, addr):
    from descartes.grid import parse_address
    c, r = parse_address(addr)
    return ws.value_at(c, r)


class TestParser:
    def test_precedence(self):
        ws, _ = make_ws({(0, 0): "2+3*4^2"})
        assert val(ws, "A1") == 50.0

    def test_unary_and_parens(self):
        ws, _ = make_ws({(0, 0): "-(2+3)*2"})
        assert val(ws, "A1") == -10.0

    def test_concat(self):
        ws, _ = make_ws({(0, 0): '+"a"&"b"', (1, 0): '("a"&"b")="ab"'})
        assert val(ws, "A1") == "ab"
        assert val(ws, "B1") == 1.0

    def test_comparison_ops(self):
        ws, _ = make_ws({(0, 0): "1<2", (1, 0): "2<=2", (2, 0): "3>4",
                         (3, 0): "4>=5", (4, 0): '+"A"="a"', (5, 0): "1<>2"})
        assert [val(ws, f"{c}1") for c in "ABCDEF"] == [1.0, 1.0, 0.0, 0.0, 1.0, 1.0]

    def test_logic_ops(self):
        ws, _ = make_ws({(0, 0): "1#AND#1", (1, 0): "1#OR#0", (2, 0): "#NOT#0"})
        assert val(ws, "A1") == 1.0
        assert val(ws, "B1") == 1.0
        assert val(ws, "C1") == 1.0

    def test_percent(self):
        ws, _ = make_ws({(0, 0): "50%+50%"})
        assert val(ws, "A1") == 1.0

    def test_syntax_error(self):
        with pytest.raises(FormulaSyntaxError):
            parse("1+")
        with pytest.raises(FormulaSyntaxError):
            parse("@NOSUCHFUNC(")

    def test_serialize_roundtrip(self):
        for raw in ["+A1*2", "@SUM(A1,A5..B7)+1", '"x"&B2', "-(A1+B1)^2"]:
            assert serialize(parse(raw)) is not None
            # re-parse must succeed
            parse(serialize(parse(raw)))


class TestRefsAndShift:
    def test_collect_refs(self):
        refs, ranges = collect_refs(parse("+A1+B2..C3+$D$4"))
        assert (0, 0) in refs and (3, 3) in refs
        assert ((1, 1), (2, 2)) in ranges

    def test_shift_relative_absolute(self):
        # formula at B2 = +A1*2+$B$1 ; copying to C3 shifts relative refs by (1,1)
        out = shift_raw("+A1*2+$B$1", 1, 1)
        ws, _ = make_ws({(0, 0): "10", (1, 0): "99", (1, 1): "+A1*2+$B$1", (2, 2): out})
        assert val(ws, "B2") == 10 * 2 + 99
        assert val(ws, "C3") == val(ws, "B2") * 2 + 99  # +B2*2+$B$1

    def test_shift_clamps(self):
        out = shift_raw("+A1", -5, 0)
        assert "A1" in out  # clamped to column A


class TestRecalc:
    def test_sum_range(self):
        ws, _ = make_ws({(0, 0): "123", (0, 1): "456", (0, 2): "@SUM(A1..A2)"})
        assert val(ws, "A3") == 579.0

    def test_dependency_chain(self):
        ws, _ = make_ws({(0, 0): "1", (0, 1): "+A1+1", (0, 2): "+A2*10"})
        assert val(ws, "A3") == 20.0

    def test_dirty_propagation(self):
        ws, eng = make_ws({(0, 0): "1", (0, 1): "+A1+1"})
        ws.set_entry(0, 0, "5")
        eng.note_edit((0, 0))
        eng.recalc()
        assert val(ws, "A2") == 6.0

    def test_cycle_err(self):
        ws, _ = make_ws({(0, 0): "+B1+1", (1, 0): "+A1+1"})
        assert isinstance(val(ws, "A1"), Error) and val(ws, "A1").code == "ERR"
        assert isinstance(val(ws, "B1"), Error)

    def test_self_cycle_err(self):
        ws, _ = make_ws({(0, 0): "+A1+1"})
        assert isinstance(val(ws, "A1"), Error)

    def test_iteration(self):
        ws, eng = make_ws({(0, 0): "+B1+1", (1, 0): "+A1+1"})
        ws.iterations = 3
        eng.recalc(force=True)
        assert isinstance(val(ws, "A1"), float)

    def test_manual_mode_and_force(self):
        ws = Worksheet()
        ws.recalc_mode = "manual"
        ws.set_entry(0, 0, "5")
        ws.set_entry(0, 1, "+A1*2")
        eng = Engine(ws)
        eng.register_all()
        assert eng.recalc() is False          # manual: no recalc
        assert eng.needs_calc() is True
        assert val(ws, "A2") is None
        eng.recalc(force=True)                # F9
        assert val(ws, "A2") == 10.0

    def test_div_zero_err(self):
        ws, _ = make_ws({(0, 0): "1/0"})
        assert isinstance(val(ws, "A1"), Error)

    def test_na_propagates(self):
        ws, _ = make_ws({(0, 0): "@NA", (0, 1): "+A1+1"})
        assert val(ws, "A2").code == "NA"

    def test_implicit_intersection(self):
        # single-row range: formula cell's column intersects the range row
        ws, _ = make_ws({(0, 0): "1", (1, 0): "10", (2, 0): "100",
                         (1, 4): "+A1..C1"})
        assert val(ws, "B5") == 10.0
        # multi-row range not covering the formula row -> first cell fallback
        ws2, _ = make_ws({(0, 0): "1", (0, 1): "2", (1, 0): "10", (1, 1): "20",
                          (0, 5): "+A1..B2"})
        assert val(ws2, "A6") == 1.0

    def test_blank_is_zero(self):
        ws, _ = make_ws({(0, 0): "+A2+5"})
        assert val(ws, "A1") == 5.0

    def test_performance_20k(self):
        ws = Worksheet()
        NC, NR = 100, 200   # 20k cells
        ws.set_entry(0, 0, "1")
        from descartes.grid import col_letters
        for r in range(NR):
            for c in range(1, NC):
                ws.set_entry(c, r, f"+{col_letters(c-1)}{r+1}+1")
        eng = Engine(ws)
        eng.register_all()
        t0 = time.perf_counter()
        eng.recalc(force=True)
        dt = time.perf_counter() - t0
        assert ws.value_at(NC - 1, 0) == float(NC)
        # spec target: full-grid recalc of 20k cells < 300 ms
        assert dt < 0.300, f"recalc of 20k cells took {dt*1000:.1f} ms (target < 300 ms)"
        print(f"\n20k-cell full recalc: {dt*1000:.1f} ms")
