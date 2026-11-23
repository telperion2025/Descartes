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
