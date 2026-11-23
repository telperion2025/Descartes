"""Regression tests for review round 2: crash paths (TypeError/OverflowError),
overlapping /MOVE, formula-reference adjustment on insert/delete, UTF-8
detection, and load-time state restoration."""

import json

import pytest

from conftest import FakeWindow, draw, press, type_text
from descartes import fmt
from descartes.engine import Engine
from descartes.grid import Worksheet


def _is_err(v):
    return getattr(v, "_is_lotus_error", False)


def _eval(ws, raw, key=(0, 0)):
    ws.set_entry(*key, raw)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws.value_at(*key)


class TestNoCrashOnBadArgs:
    @pytest.mark.parametrize("raw", [
        '+@STRING(1,"x")', '+@INDEX(A1..A3,"x")', '+@LEFT("ab","x")',
        '+@RIGHT("ab","x")', '+@MID("abc","x",2)', '+@REPEAT("a","x")',
        '+@CHAR("x")', '+@DATE("a",1,1)', '+@TIME("a",1,1)', '+@ROUND("x",2)',
    ])
    def test_text_arg_yields_err_not_exception(self, raw):
        ws = Worksheet()
        v = _eval(ws, raw)
        assert _is_err(v), (raw, v)

    def test_app_survives_bad_formula_entry(self, make_app):
        app, win = make_app()
        type_text(app, '+@LEFT("ab","x")')
        press(app, "ENTER")
        assert app.running
        assert app.mode == "READY"
        assert _is_err(app.sheet.value_at(0, 0))
        draw(app)


class TestInfinity:
    def test_arithmetic_overflow_is_err(self):
        ws = Worksheet()
        assert _is_err(_eval(ws, "+1E308*10"))
        assert _is_err(_eval(ws, "+1E308+1E308"))

    def test_general_number_nonfinite(self):
        assert fmt.general_number(float("inf")) == "ERR"
        assert fmt.general_number(float("-inf")) == "ERR"
        assert fmt.general_number(float("nan")) == "ERR"

    def test_format_number_nonfinite(self):
        assert fmt.format_number(float("inf"), None) == "ERR"
        assert fmt.format_number(float("inf"), {"type": "fixed", "decimals": 2}) == "ERR"
        assert fmt.format_number(float("nan"), {"type": "comma", "decimals": 2}) == "ERR"

    def test_app_draws_after_overflow(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "+1E308*10")
        app.engine.register_all()
        app.engine.recalc(force=True)
        draw(app)
        assert app.running
        assert "ERR" in win.text()[4]


class TestOverlapMove:
    def test_move_overlapping_down(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "10")
        app.sheet.set_entry(0, 1, "20")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.cmd_copy(["A1..A2", "A2"], move=True)
        assert app.sheet.value_at(0, 0) is None
        assert app.sheet.value_at(0, 1) == 10.0
        assert app.sheet.value_at(0, 2) == 20.0

    def test_move_non_overlapping_still_works(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "7")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.cmd_copy(["A1", "C1"], move=True)
        assert app.sheet.value_at(0, 0) is None
        assert app.sheet.value_at(2, 0) == 7.0


class TestInsertDeleteAdjustsRefs:
    def test_insert_row_shifts_formula_refs(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(0, 1, "+A1*2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        assert app.sheet.value_at(0, 1) == 10.0
        app.cmd_insert_row(0)
        assert app.sheet.raw_at(0, 2) is not None and "A2" in app.sheet.raw_at(0, 2)
        assert app.sheet.value_at(0, 2) == 10.0

    def test_insert_col_shifts_formula_refs(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(1, 0, "+A1*2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.cmd_insert_col(0)
        assert app.sheet.value_at(1, 0) == 5.0
        assert app.sheet.value_at(2, 0) == 10.0

    def test_insert_expands_spanning_range(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(1, 0, "+@SUM(A1..A3)")
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        assert app.sheet.value_at(1, 0) == 3.0
        app.cmd_insert_row(1)
        assert app.sheet.value_at(1, 0) == 3.0
        assert "A1..A4" in app.sheet.raw_at(1, 0)

    def test_delete_row_refs_into_band_become_err(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(0, 1, "+A1*2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.cmd_delete_row(0)
        assert _is_err(app.sheet.value_at(0, 0))

    def test_delete_row_shifts_refs_below_band(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "9")
        app.sheet.set_entry(0, 1, "x")
        app.sheet.set_entry(0, 2, "+A1*2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        assert app.sheet.value_at(0, 2) == 18.0
        app.cmd_delete_row(1)
        assert app.sheet.value_at(0, 1) == 18.0


class TestUtf8Detection:
    def test_utf8_flag_follows_locale(self, fake_curses, monkeypatch):
        import locale
        from descartes.app import App
        monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "C")
        win = FakeWindow(25, 80)
        app = App(win, None)
        app.screen.init()
        assert app.screen.utf8 is False
        monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "en_US.UTF-8")
        app.screen.init()
        assert app.screen.utf8 is True


class TestLoadRestoresState:
    def test_load_restores_graph_window_print_cursor(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.graph.type = "BAR"
        app.graph.series.append({"name": "S", "range": ((0, 0), (0, 0))})
        app.set_split("H")
        app.print_settings["header"] = "HDR"
        app.cur = (3, 7)
        path = str(tmp_path / "book.vg1")
        app.cmd_save(path)
        app2, win2 = make_app()
        app2.load_file(path)
        assert app2.graph.type == "BAR"
        assert len(app2.graph.series) == 1
        assert app2.split == "H"
        assert app2.print_settings["header"] == "HDR"
        assert app2.cur == (3, 7)
        assert app2.sheet.value_at(0, 0) == 5.0

    def test_load_clamps_out_of_range_cursor(self, make_app, tmp_path):
        doc = {
            "format": "descartes-vg1", "version": 1,
            "sheets": {"A": {"cells": [], "columns": {}}},
            "names": {}, "recalc": {"mode": "auto", "iterations": 0},
            "cursor": [999, 99999], "graphs": [], "print": {}, "window": {},
        }
        path = tmp_path / "bad.vg1"
        path.write_text(json.dumps(doc))
        app, win = make_app()
        app.load_file(str(path))
        assert app.cur == (255, 8191)
        draw(app)
        assert app.running


class TestSubmitEntrySyntaxError:
    def test_bad_formula_preserves_cell_and_stays_in_edit(self, make_app):
        app, win = make_app()
        type_text(app, "5")
        press(app, "ENTER")
        press(app, "UP")
        press(app, "F2")
        press(app, "HOME")
        press(app, "DEL")
        type_text(app, "+A1+")
        press(app, "ENTER")
        assert app.running
        assert app.mode == "EDIT"
        assert app.sheet.value_at(0, 0) == 5.0
        assert app.error.startswith("ERROR")


class TestImportLabels:
    def test_dif_string_stays_label(self, tmp_path):
        from descartes.grid import LABEL
        from descartes.persist import read_dif
        p = tmp_path / "t.dif"
        p.write_text('TABLE\r\n0,1\r\n"x"\r\nVECTORS\r\n0,1\r\n"COL"\r\n'
                     'TUPLES\r\n0,1\r\nDATA\r\n0,0\r\n""\r\nS\r\n"+1+1"\r\n-1,0\r\nEOD\r\n')
        ws = read_dif(str(p))
        assert ws.kind_at(0, 0) == LABEL
        assert ws.value_at(0, 0) == "+1+1"

    def test_text_import_strings_stay_labels(self, tmp_path):
        from descartes.grid import LABEL, NUMBER
        from descartes.persist import read_text_import
        p = tmp_path / "t.txt"
        p.write_text('"+1+1",plain,42,"50%"\n')
        ws = read_text_import(str(p))
        assert ws.kind_at(0, 0) == LABEL and ws.value_at(0, 0) == "+1+1"
        assert ws.kind_at(1, 0) == LABEL and ws.value_at(1, 0) == "plain"
        assert ws.kind_at(2, 0) == NUMBER and ws.value_at(2, 0) == 42.0
        assert ws.kind_at(3, 0) == LABEL and ws.value_at(3, 0) == "50%"


class TestWk1ExportErrFormula:
    def test_uncompilable_formula_exported_not_dropped(self, tmp_path):
        from descartes.engine import Engine
        from descartes.persist import read_wk1, write_wk1
        ws = Worksheet()
        ws.set_entry(0, 0, "+1/0")
        ws.set_entry(0, 1, "+B1++")
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        path = str(tmp_path / "e.wk1")
        write_wk1(path, ws, eng)
        ws2 = read_wk1(path)["sheets"]["A"]
        assert ws2.cell(0, 1) is not None
        assert "ERR" in (ws2.raw_at(0, 1) or "")


class TestSortBlanksAndRefs:
    def test_blanks_sort_last_both_directions(self, make_app):
        from descartes.grid import split_range
        app, win = make_app()
        app.sheet.set_entry(0, 0, "3")
        app.sheet.set_entry(0, 2, "1")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.sort_state.update(range=split_range("A1..A3"))
        app._set_sort_key(0, ("A1", "YES"))
        app.cmd_sort_go()
        assert [app.sheet.value_at(0, r) for r in range(3)] == [1.0, 3.0, None]
        app._set_sort_key(0, ("A1", "NO"))
        app.cmd_sort_go()
        assert [app.sheet.value_at(0, r) for r in range(3)] == [3.0, 1.0, None]

    def test_sort_shifts_formula_refs_with_row(self, make_app):
        from descartes.grid import split_range
        app, win = make_app()
        app.sheet.set_entry(0, 0, "2")
        app.sheet.set_entry(0, 1, "1")
        app.sheet.set_entry(1, 0, "+A1")
        app.sheet.set_entry(1, 1, "+A2")
        app.engine.register_all()
        app.engine.recalc(force=True)
        app.sort_state.update(range=split_range("A1..B2"))
        app._set_sort_key(0, ("A1", "YES"))
        app.cmd_sort_go()
        assert app.sheet.value_at(0, 0) == 1.0
        assert app.sheet.value_at(1, 0) == 1.0
        assert "A1" in app.sheet.raw_at(1, 0)


class TestMouseDragAndCtrlWheel:
    def test_drag_extends_selection(self, make_app):
        from descartes.keys import KeyEvent, MouseEvent
        from descartes.screen import P_CURSOR, P_HILITE
        app, win = make_app()
        app.handle(KeyEvent("mouse", mouse=MouseEvent(7, 4, button=0, press=True)))
        assert app.cur == (0, 0)
        app.handle(KeyEvent("mouse", mouse=MouseEvent(7 + 2 * 9, 4 + 2, button=0,
                                                       press=True, drag=True)))
        assert app.cur == (2, 2)
        assert app.sel_range == ((0, 0), (2, 2))
        draw(app)
        assert win.attrs[4][7] == app.screen.a(P_HILITE)
        assert win.attrs[6][7 + 2 * 9] == app.screen.a(P_CURSOR)

    def test_ctrl_wheel_scrolls_horizontally(self, make_app):
        from descartes.keys import KeyEvent, MouseEvent
        app, win = make_app()
        app.top = (5, 0)
        app.handle(KeyEvent("mouse", mouse=MouseEvent(10, 10, wheel_up=True,
                                                       modifiers={"ctrl"})))
        assert app.top[0] == 4
        app.handle(KeyEvent("mouse", mouse=MouseEvent(10, 10, wheel_down=True,
                                                       modifiers={"ctrl"})))
        assert app.top[0] == 5


class TestMenuAndMisc:
    def test_graph_options_has_format(self):
        from descartes.menus import MENU_TREE
        graph = next(m for m in MENU_TREE if m.label == "GRAPH")
        opts = next(m for m in graph.children if m.label == "OPTIONS")
        assert any(m.label == "FORMAT" for m in opts.children)

    def test_name_delete_unknown_reports_error(self, make_app):
        app, win = make_app()
        app._actions["r_name_delete"](["NOPE"])
        assert app.error != ""
        app.names["TOTAL"] = {"sheet": "A", "range": ((0, 0), (0, 0))}
        app._actions["r_name_delete"](["total"])
        assert "TOTAL" not in app.names
        assert app.error == ""

    def test_control_panel_shows_addr_colon_mode(self, make_app):
        app, win = make_app()
        draw(app)
        assert win.text()[1].startswith("A1: READY")
