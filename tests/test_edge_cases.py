"""Extensive edge-case tests: addressing, parser limits, engine cycles, formats,
persistence round-trips, /DATA edges, app-level interactions, raw-input hardening."""

import json
import os

import pytest

from conftest import char, draw, press, type_text
from descartes import fmt, persist
from descartes.data_cmd import fill, frequency, query, regression, sort_range
from descartes.engine import (Engine, FormulaSyntaxError, adjust_refs, parse,
                              serialize, shift_raw)
from descartes.grid import (Worksheet, classify_entry, col_index, col_letters,
                            is_valid_name, parse_address, split_range,
                            split_range_any)


def _eval(raw, key=(0, 0)):
    ws = Worksheet()
    ws.set_entry(*key, raw)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws.value_at(*key)


def _is_err(v):
    return getattr(v, "_is_lotus_error", False)


class TestAddressing:
    def test_col_roundtrip(self):
        for c in (0, 1, 25, 26, 255):
            assert col_index(col_letters(c)) == c
        assert col_letters(255) == "IV"
        with pytest.raises(ValueError):
            col_index("IW")
        with pytest.raises(ValueError):
            col_letters(256)

    @pytest.mark.parametrize("bad", ["", "A", "1A", "A0", "A8193", "IW1", "A1B2", "A-1"])
    def test_parse_address_rejects(self, bad):
        with pytest.raises(ValueError):
            parse_address(bad)

    @pytest.mark.parametrize("good,expect", [("A1", (0, 0)), ("$A$1", (0, 0)),
                                              ("IV8192", (255, 8191)), ("b3", (1, 2))])
    def test_parse_address_ok(self, good, expect):
        assert parse_address(good) == expect

    def test_split_range_normalizes(self):
        assert split_range("B2..A1") == ((0, 0), (1, 1))
        assert split_range("C3") == ((2, 2), (2, 2))
        with pytest.raises(ValueError):
            split_range("A1..B2..C3")

    def test_split_range_any(self):
        assert split_range_any("A1..B2") == (None, (0, 0), (1, 1))
        assert split_range_any("B!A1..B2") == (("B", "B"), (0, 0), (1, 1))
        assert split_range_any("A:C!A1..B2") == (("A", "C"), (0, 0), (1, 1))
        assert split_range_any("$A:$C!$A$1") == (("A", "C"), (0, 0), (0, 0))
        with pytest.raises(ValueError):
            split_range_any("bogus")

    @pytest.mark.parametrize("raw,kind", [("", "EMPTY"), ("'x", "LABEL"), ("^x", "LABEL"),
                                          ('"x', "LABEL"), ("50%", "NUMBER"), ("+5", "NUMBER"),
                                          ("-.5", "NUMBER"), ("@SUM(A1)", "FORMULA"),
                                          ("$A$1", "FORMULA"), ("#AND#", "FORMULA"),
                                          ("1.2.3", "FORMULA"), ("hello", "LABEL")])
    def test_classify_entry(self, raw, kind):
        assert classify_entry(raw)[0] == kind

    def test_is_valid_name(self):
        assert is_valid_name("TOTAL") and is_valid_name("Q_1") and is_valid_name("ZZ9x")
        for bad in ("", "A1", "IV8192", "A!A1", "A:B!A1", "1X", "a b", "x" * 16):
            assert not is_valid_name(bad), bad


class TestParserEdges:
    def test_deep_nesting_is_syntax_error_not_crash(self):
        with pytest.raises(FormulaSyntaxError):
            parse("(" * 500 + "1" + ")" * 500)
        assert parse("(" * 50 + "1" + ")" * 50) is not None

    @pytest.mark.parametrize("bad", ['"unterminated', "@", "@1x(", "1.2.3+", "#BAD#",
                                     "A1 B2", "()", "+"])
    def test_bad_formulas_rejected(self, bad):
        with pytest.raises(FormulaSyntaxError):
            parse(bad)

    def test_precedence_quirks(self):
        assert _eval("-2^2") == 4.0          # 1-2-3: unary minus binds tighter than ^
        assert _eval("2^3^2") == 64.0        # left-associative ^
        assert _eval("2+3*4") == 14.0
        assert _eval("1+2#AND#3") == 1.0
        assert _eval('+"a"&"b"&1') == "ab1"
        assert _eval("1<2") == 1.0 and _eval("2<1") == 0.0
        assert _eval('+"B">"a"') == 1.0      # case-insensitive text compare

    def test_cycles_become_err(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "+B1")
        ws.set_entry(1, 0, "+A1")
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        assert _is_err(ws.value_at(0, 0)) and _is_err(ws.value_at(1, 0))
        ws2 = Worksheet()
        ws2.set_entry(0, 0, "+A1+1")
        eng2 = Engine(ws2)
        eng2.register_all()
        eng2.recalc(force=True)
        assert _is_err(ws2.value_at(0, 0))

    def test_iteration_mode_converges(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "+A1+1")
        ws.recalc_mode = "manual"
        ws.iterations = 5
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        assert ws.value_at(0, 0) == 5.0

    def test_blank_and_text_arithmetic(self):
        assert _eval("+A9+1") == 1.0         # blank = 0
        ws = Worksheet()
        ws.set_entry(0, 1, "hello")
        ws.set_entry(1, 0, "+A2*2")
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        assert _is_err(ws.value_at(1, 0))    # text in arithmetic -> ERR

    def test_percent(self):
        assert _eval("50%+50%") == 1.0
        assert _eval("200%") == 2.0

    def test_serialize_roundtrips(self):
        for raw in ["+A1*2", "@SUM(A1,A5..B7)+1", '"x"&B2', "-(A1+B1)^2", "+TOTAL*2",
                    "+@SUM(A:C!A1..B2)", "+B!A1", "+1#OR#0#AND#1", "50%*2", "+@IF(A1,1,2)"]:
            assert serialize(parse(raw))
            parse(serialize(parse(raw)))

    def test_shift_preserves_dollar_and_clamps(self):
        assert shift_raw("+$A$1+A1", 1, 1) == "+$A$1+B2"
        assert shift_raw("+A1", -5, -5) == "+A1"     # clamped at edge
        assert shift_raw("+TOTAL", 3, 3) == "+TOTAL"  # names don't shift

    def test_adjust_refs_delete_band(self):
        assert "@ERR()" in serialize(adjust_refs(parse("+A1*2"), "row", 0, -1, True))


class TestFunctionEdges:
    @pytest.mark.parametrize("raw", ["+@IF(1)", "+@CHOOSE(0,1,2)", "+@CHOOSE(9,1,2)",
                                     "+@MOD(1,0)", "+@SQRT(-1)", "+@LN(0)", "+@LOG(0)",
                                     "+@DATE(2024,13,1)", "+@LEFT(\"a\",-1)", "+@MID(\"a\",0,1)",
                                     "+@INDEX(A1..A3,99)", "+@HLOOKUP(1,A1..A1,0)",
                                     "+@NOSUCHFUNC(1)", "+@SUM()", "+1/0", "+@ERR"])
    def test_edge_args_give_err_or_value_not_crash(self, raw):
        v = _eval(raw)
        assert v is not None

    def test_lookup_approximate(self):
        ws = Worksheet()
        for r, (k, v) in enumerate((("a", 1), ("c", 2), ("e", 3))):
            ws.set_entry(0, r, k)
            ws.set_entry(1, r, str(v))
        ws.set_entry(3, 0, '+@VLOOKUP("d",A1..B3,2)')
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        assert ws.value_at(3, 0) == 2.0

    def test_date_serials_lotus_epoch(self):
        assert fmt.date_to_lotus(fmt.lotus_to_date(1)) == 1
        d60 = fmt.lotus_to_date(60)
        assert (d60.year, d60.month) == (1900, 3) and d60.day == 1  # fictitious 2-29 maps forward
        assert fmt.lotus_to_date(61) == fmt.lotus_to_date(60)
        assert _eval("+@DATE(1900,1,1)") == 1.0
        assert _eval("+@DATE(2024,1,1)") == fmt.date_to_lotus(__import__("datetime").date(2024, 1, 1))
        assert _eval("+@DATE(99,1,1)") == _eval("+@DATE(1999,1,1)")
        assert _eval("+@VALUE(\"50%\")") == 0.5


class TestFormatEdges:
    def test_all_formats_render(self):
        assert fmt.format_number(1234.5, {"type": "comma", "decimals": 2}) == "1,234.50"
        assert fmt.format_number(0.5, {"type": "percent", "decimals": 0}) == "50%"
        assert fmt.format_number(1500, {"type": "currency", "decimals": 2}) == "$1,500.00"
        assert fmt.format_number(12345, {"type": "sci", "decimals": 1}) == "1.2E+04"
        assert fmt.format_number(5, {"type": "hidden"}) == ""
        assert fmt.format_number(5, {"type": "fixed", "decimals": 0}) == "5"
        assert fmt.format_date(1) == "01-Jan-00"
        assert fmt.format_time(0.5) == "12:00:00"

    def test_nonfinite_renders_err(self):
        for v in (float("inf"), float("-inf"), float("nan")):
            assert fmt.display(v, None) == "ERR"
            assert fmt.display(v, {"type": "date", "style": "dd-mmm-yy"}) == "ERR"
            assert fmt.display(v, {"type": "time"}) == "ERR"


class TestPersistEdges:
    def test_vg1_full_roundtrip(self, tmp_path):
        ws = Worksheet("A")
        ws.set_entry(0, 0, "123")
        ws.set_entry(1, 0, "'Label")
        ws.set_entry(2, 0, "+A1*2")
        ws.set_entry(3, 0, "+1/0")          # ERR value
        ws.set_entry(4, 0, "50%")
        ws.widths[0] = 12
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        ws2b = Worksheet("B")
        ws2b.set_entry(0, 0, "=x".replace("=", "+") + "A!A1")
        names = {"TOTAL": {"sheet": "A", "range": ((0, 0), (2, 0))},
                 "SPAN": {"sheet": "A", "range": ((0, 0), (0, 0)), "sheets": ["A", "B"]}}
        p = str(tmp_path / "x.vg1")
        persist.save_vg1(p, {"A": ws, "B": ws2b}, names, cursor=(2, 3), active="B")
        data = persist.load_vg1(p)
        assert data["active"] == "B"
        assert data["sheets"]["A"].value_at(2, 0) == 246.0
        assert _is_err(data["sheets"]["A"].value_at(3, 0))
        assert data["sheets"]["A"].value_at(4, 0) == 0.5
        assert data["sheets"]["A"].widths[0] == 12
        assert data["names"]["SPAN"]["sheets"] == ["A", "B"]
        assert data["cursor"] == (2, 3)

    def test_vg1_rejects_wrong_format(self, tmp_path):
        p = tmp_path / "bad.vg1"
        p.write_text(json.dumps({"format": "other"}))
        with pytest.raises(ValueError):
            persist.load_vg1(str(p))

    def test_wk1_write_read_roundtrip(self, tmp_path):
        ws = Worksheet()
        ws.set_entry(0, 0, "42")
        ws.set_entry(1, 0, "2.5")
        ws.set_entry(2, 0, "'hi")
        ws.set_entry(3, 0, "+A1*2")
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        p = str(tmp_path / "x.wk1")
        persist.write_wk1(p, ws, eng)
        ws2 = persist.read_wk1(p)["sheets"]["A"]
        assert ws2.value_at(0, 0) == 42.0
        assert ws2.value_at(1, 0) == 2.5
        assert ws2.value_at(2, 0) == "hi"
        assert ws2.raw_at(3, 0).startswith("+")
        eng2 = persist.attach_engine(ws2)
        assert ws2.value_at(3, 0) == 84.0

    def test_text_import_edges(self, tmp_path):
        p = tmp_path / "t.txt"
        p.write_text('\n1,,3\n"a""b",\n')
        ws = persist.read_text_import(str(p))
        assert ws.value_at(0, 1) == 1.0
        assert ws.cell(1, 1) is None
        assert ws.value_at(2, 1) == 3.0
        assert ws.value_at(0, 2) == 'a"b'

    def test_import_file_dispatch(self, tmp_path):
        p = tmp_path / "x.dif"
        p.write_text('TABLE\r\n0,1\r\n"x"\r\nVECTORS\r\n0,1\r\n"C"\r\nTUPLES\r\n0,1\r\n'
                     'DATA\r\n0,0\r\n""\r\nV\r\n7\r\n-1,0\r\nEOD\r\n')
        assert persist.import_file(str(p))["sheets"]["A"].value_at(0, 0) == 7.0


class TestDataCmdEdges:
    def test_fill_step_zero_bounded(self):
        ws = Worksheet()
        eng = Engine(ws)
        fill(ws, eng, ((0, 0), (0, 4)), 9.0, 0.0)
        assert [ws.value_at(0, r) for r in range(5)] == [9.0] * 5

    def test_fill_with_stop(self):
        ws = Worksheet()
        eng = Engine(ws)
        fill(ws, eng, ((0, 0), (0, 9)), 1.0, 2.0, 5.0)
        assert [ws.value_at(0, r) for r in range(5)] == [1.0, 3.0, 5.0, None, None]

    def test_sort_mixed_types_and_stability(self):
        ws = Worksheet()
        eng = Engine(ws)
        ws.set_entry(0, 0, "+1/0")
        ws.set_entry(0, 1, "b")
        ws.set_entry(0, 2, "2")
        ws.set_entry(0, 3, "A")
        ws.set_entry(0, 4, "2")
        sort_range(ws, eng, ((0, 0), (0, 4)), [(0, True)])
        vals = [ws.value_at(0, r) for r in range(5)]
        assert vals[0] == 2.0 and vals[1] == 2.0      # numbers first, stable
        assert vals[2] == "A" and vals[3] == "b"      # then A-Z, then a-z
        assert _is_err(vals[4])

    def test_query_wildcard_same_sheet_criterion(self):
        ws = Worksheet()
        eng = Engine(ws)
        ws.set_entry(0, 0, "name")
        for r, v in enumerate(("apple", "Apricot", "banana", "APPLE pie")):
            ws.set_entry(0, r + 1, v)
        ws.set_entry(2, 0, "name")
        ws.set_entry(2, 1, "app*")
        assert query(ws, eng, ((0, 0), (0, 4)), ((2, 0), (2, 1))) == [1, 4]

    def test_query_delete_and_extract_unique(self, tmp_path):
        ws = Worksheet()
        eng = Engine(ws)
        ws.set_entry(0, 0, "v")
        for r, v in enumerate((1, 1, 2, 2, 3)):
            ws.set_entry(0, r + 1, str(v))
        ws.set_entry(2, 0, "v")
        ws.set_entry(2, 1, "1")
        m = query(ws, eng, ((0, 0), (0, 5)), ((2, 0), (2, 1)), action="delete")
        assert m == [1, 2]
        assert [ws.value_at(0, r) for r in range(1, 6)] == [None, None, 2.0, 2.0, 3.0]
        ws2 = Worksheet()
        eng2 = Engine(ws2)
        ws2.set_entry(0, 0, "v")
        for r, v in enumerate((1, 1, 2, 2, 3)):
            ws2.set_entry(0, r + 1, str(v))
        ws2.set_entry(2, 0, "v")
        ws2.set_entry(2, 1, "")
        m2 = query(ws2, eng2, ((0, 0), (0, 5)), ((2, 0), (2, 1)),
                   output_rng=((4, 0), (4, 4)), unique=True, action="extract")
        assert m2 == [1, 2, 3, 4, 5]
        assert ws2.value_at(4, 0) == "v"
        assert [ws2.value_at(4, r) for r in range(1, 4)] == [1.0, 2.0, 3.0]

    def test_frequency_vertical(self):
        ws = Worksheet()
        eng = Engine(ws)
        for r, v in enumerate((1, 2, 2, 5, 9)):
            ws.set_entry(0, r, str(v))
        ws.set_entry(2, 0, "2")
        ws.set_entry(2, 1, "5")
        counts = frequency(ws, eng, ((0, 0), (0, 4)), ((2, 0), (2, 1)))
        assert counts == [3, 1, 1]
        assert ws.value_at(3, 0) == 3.0 and ws.value_at(3, 2) == 1.0

    def test_regression(self):
        ws = Worksheet()
        eng = Engine(ws)
        for r, (x, y) in enumerate(((1, 2), (2, 4), (3, 6))):
            ws.set_entry(0, r, str(x))
            ws.set_entry(1, r, str(y))
        res = regression(ws, eng, ((0, 0), (0, 2)), ((1, 0), (1, 2)), (3, 0))
        assert abs(res["slope"] - 2.0) < 1e-9 and abs(res["intercept"]) < 1e-9
        assert abs(res["r2"] - 1.0) < 1e-9
        with pytest.raises(ValueError):
            regression(ws, eng, ((0, 0), (0, 0)), ((1, 0), (1, 0)), (3, 0))


class TestGridStructuralEdges:
    def test_col_width_clamped(self):
        ws = Worksheet()
        ws.set_col_width(0, 0)
        assert ws.col_width(0) == 0
        ws.set_col_width(0, 999)
        assert ws.col_width(0) == 240
        ws.set_col_width(0, -5)
        assert ws.col_width(0) == 0
        ws.set_col_width(0, 9)
        assert ws.col_width(0) == 9 and 0 not in ws.widths

    def test_insert_at_edges(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 8191, "last")
        app.cmd_insert_row(8191)
        assert app.sheet.value_at(0, 8191) is None   # pushed off the bottom
        app.cmd_delete_row(0)
        assert app.sheet.value_at(0, 0) is None


class TestAppInteractionEdges:
    def test_point_mode_anchor_extend_submit(self, make_app):
        from descartes.keys import KeyEvent
        app, win = make_app()
        app.enter_point(for_prompt=0)
        assert app.mode == "POINT"
        press(app, "DOWN")
        press(app, "RIGHT")
        char(app, ".")
        press(app, "UP")
        text = app.submit_point()
        assert text == "B1..B2"

    def test_prompt_backspace_goes_back(self, make_app):
        from descartes.app import Prompt
        seen = []
        p = Prompt([{"text": "a", "kind": "text"}, {"text": "b", "kind": "text"}],
                   lambda ans: seen.append(ans))
        app, win = make_app()
        app.prompt = p
        app.mode = "DIALOG"
        type_text(app, "one")
        press(app, "ENTER")
        assert app.prompt.idx == 1
        press(app, "BACKSPACE")       # empty entry -> back to previous prompt
        assert app.prompt.idx == 0
        type_text(app, "uno")
        press(app, "ENTER")
        press(app, "ENTER") if False else None
        type_text(app, "dos")
        press(app, "ENTER")
        assert seen == [["uno", "dos"]]
        assert app.mode == "READY"

    def test_esc_in_dialog_cancels_chain(self, make_app):
        app, win = make_app()
        app.prompt_chain([{"text": "x", "kind": "text"}], lambda a: None)
        type_text(app, "abc")
        press(app, "ESC")
        assert app.mode == "READY"
        assert app.prompt is None

    def test_error_mode_recovers_on_key(self, make_app):
        app, win = make_app()
        app.error_msg("boom")
        assert app.mode == "ERROR"
        press(app, "ENTER")
        assert app.mode == "READY" and app.error == ""

    def test_resize_too_small_message(self, make_app):
        app, win = make_app()
        win.h, win.w = 24, 79
        app.handle_key("RESIZE")
        assert "at least 80x25" in app.error

    def test_mouse_header_and_gutter(self, make_app):
        from descartes.keys import KeyEvent, MouseEvent
        app, win = make_app()
        app.handle(KeyEvent("mouse", mouse=MouseEvent(7 + 9, 3, button=0, press=True)))
        assert app.cur[0] == 1          # clicked column B header
        app.handle(KeyEvent("mouse", mouse=MouseEvent(2, 4 + 3, button=0, press=True)))
        assert app.cur[1] == 3          # clicked row gutter 4

    def test_double_click_cell_edits(self, make_app):
        from descartes.keys import KeyEvent, MouseEvent
        app, win = make_app()
        type_text(app, "42")
        press(app, "ENTER")
        app.handle(KeyEvent("mouse", mouse=MouseEvent(7, 4, button=0, press=True)))
        app.handle(KeyEvent("mouse", mouse=MouseEvent(7, 4, button=0, press=True)))
        assert app.mode == "EDIT" and app.entry == "42"

    def test_print_to_file(self, make_app, tmp_path):
        app, win = make_app()
        type_text(app, "123")
        press(app, "ENTER")
        out = str(tmp_path / "p.txt")
        app.cmd_print("A1", dest="file:" + out)
        assert "123" in open(out).read()

    def test_combine_add(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "10")
        other = Worksheet()
        other.set_entry(0, 0, "5")
        p = str(tmp_path / "o.vg1")
        persist.save_vg1(p, {"A": other})
        app.cmd_combine(p, ((0, 0), (0, 0)), (0, 0), "add")
        app._recalc(force=True)
        assert app.sheet.value_at(0, 0) == 15.0


class TestRawReaderHardening:
    def _pipe(self):
        r, w = os.pipe()
        from descartes.keys import RawReader
        return RawReader(r, escdelay_ms=5), w, r

    def test_unknown_csi_swallowed(self):
        rd, w, r = self._pipe()
        os.write(w, b"\x1b[99~")
        ev = rd.read_event()
        assert ev is None or (ev.kind != "char")   # nothing leaks as chars
        os.write(w, b"z")
        ev = rd.read_event()
        assert ev is not None and ev.kind == "char" and ev.ch == "z"
        os.close(w); os.close(r)

    def test_unknown_ss3_swallowed(self):
        rd, w, r = self._pipe()
        os.write(w, b"\x1bOZ")
        ev = rd.read_event()
        assert ev is None or ev.kind != "char"
        os.close(w); os.close(r)

    def test_eof_sets_flag(self):
        rd, w, r = self._pipe()
        os.write(w, b"x")
        os.close(w)
        assert rd.read_event().ch == "x"
        assert rd.read_event() is None
        assert rd.eof
        os.close(r)
