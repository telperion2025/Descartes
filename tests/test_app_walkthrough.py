"""Acceptance walkthrough (spec section 12) driven through App with a fake
curses screen: entry/formula, copy semantics, save/reload, fill+sort,
graph view, mouse behaviors."""

import os

import pytest

from conftest import char, draw, press, screen_text, type_text
from descartes.keys import KeyEvent, MouseEvent
from descartes.grid import parse_address


def grid_row(win, sheet_row, app):
    from descartes.screen import ROW_GRID0
    return win.text()[ROW_GRID0 + sheet_row]


class TestLayout:
    def test_ready_layout(self, make_app):
        app, win = make_app()
        snap = screen_text(app, win)
        lines = snap.split("\n")
        assert lines[0].strip() == "Descartes 1-2-3"          # row 1 menu bar
        assert "A1" in lines[1] and "READY" in lines[1]        # row 2 control panel
        assert lines[3][11] == "A" and lines[3][7] == " "      # row 4 header 'A' centered in col
        assert lines[4].startswith("     1|")                  # row 5 gutter
        assert len(lines) == 25
        # indicator line row 23 has OVR/INS and a clock at right
        assert "INS" in lines[22]

    def test_terminal_too_small(self, fake_curses):
        from conftest import FakeWindow
        from descartes.app import App
        from descartes.screen import TerminalTooSmall
        app = App(FakeWindow(24, 80))
        with pytest.raises(TerminalTooSmall):
            app.screen.init()


class TestEntryAndFormula:
    def test_acceptance_2_sum(self, make_app):
        app, win = make_app()
        type_text(app, "123")
        press(app, "ENTER")
        type_text(app, "456")
        press(app, "ENTER")
        type_text(app, "@SUM(A1..A2)")
        press(app, "ENTER")
        assert app.sheet.value_at(0, 2) == 579.0
        press(app, "UP")               # cursor back to A3
        snap = screen_text(app, win)
        row = grid_row(win, 2, app)
        assert row[7:16] == "      579"   # right-aligned in 9-wide column A
        assert "@SUM(A1..A2)" in snap.split("\n")[2]  # content line shows raw text

    def test_label_and_alignment(self, make_app):
        app, win = make_app()
        type_text(app, '"right-aligned')
        press(app, "ENTER")
        assert app.sheet.cell(0, 0).align == "right"
        assert app.sheet.value_at(0, 0) == "right-aligned"

    def test_edit_mode_f2(self, make_app):
        app, win = make_app()
        type_text(app, "99")
        press(app, "ENTER")
        press(app, "UP")
        press(app, "F2")
        assert app.mode == "EDIT"
        type_text(app, "9")   # appends -> 999
        press(app, "ENTER")
        assert app.sheet.value_at(0, 0) == 999.0

    def test_esc_aborts_entry(self, make_app):
        app, win = make_app()
        type_text(app, "123")
        press(app, "ESC")
        assert app.mode == "READY"
        assert app.sheet.cell(0, 0) is None


class TestCopyMove:
    def test_acceptance_3_copy_relative_absolute(self, make_app):
        app, win = make_app()
        type_text(app, "10"); press(app, "ENTER")          # A1 = 10
        type_text(app, "+A1*2"); press(app, "ENTER")       # A2 = 20 (relative ref)
        press(app, "UP"); press(app, "UP")                 # A1
        press(app, "RIGHT")                                # B1
        type_text(app, "+$A$1+100"); press(app, "ENTER")   # B1 = 110 (absolute ref)
        # /COPY A2..A2 to B2 -> relative ref A1 becomes B1
        char(app, "/")
        press(app, "RIGHT"); press(app, "RIGHT")           # COPY
        press(app, "ENTER")                                # prompts
        assert app.mode == "DIALOG"
        type_text(app, "A2..A2"); press(app, "ENTER")
        type_text(app, "B2"); press(app, "ENTER")
        assert app.sheet.raw_at(1, 1) == "+B1*2"
        assert app.sheet.value_at(1, 1) == 220.0
        # /COPY B1..B1 to C1 -> absolute ref stays fixed
        char(app, "/")
        press(app, "RIGHT"); press(app, "RIGHT")
        press(app, "ENTER")
        type_text(app, "B1..B1"); press(app, "ENTER")
        type_text(app, "C1"); press(app, "ENTER")
        assert app.sheet.raw_at(2, 0) == "+$A$1+100"
        assert app.sheet.value_at(2, 0) == 110.0

    def test_copy_via_point_mode(self, make_app):
        app, win = make_app()
        type_text(app, "7"); press(app, "ENTER")
        char(app, "/")
        press(app, "RIGHT"); press(app, "RIGHT"); press(app, "ENTER")
        press(app, "ENTER")          # empty entry -> POINT mode for range
        assert app.mode == "POINT"
        press(app, "HOME")           # cursor to A1
        press(app, ".")              # anchor at A1
        press(app, "DOWN")
        press(app, "ENTER")          # submit A1..A2
        assert app.mode == "DIALOG"
        assert app.entry == "A1..A2"
        press(app, "ENTER")          # next prompt (To:)
        type_text(app, "C1"); press(app, "ENTER")
        assert app.sheet.value_at(2, 0) == 7.0
        assert app.sheet.cell(2, 1) is None   # blank source erased the target


class TestFileSaveReload:
    def test_acceptance_4_save_reload(self, make_app, tmp_path):
        app, win = make_app()
        type_text(app, "123"); press(app, "ENTER")
        type_text(app, "456"); press(app, "ENTER")
        type_text(app, "@SUM(A1..A2)"); press(app, "ENTER")
        app.sheet.set_col_width(0, 12)
        app.sheet.get_or_make(0, 1).fmt = {"type": "fixed", "decimals": 1}
        path = str(tmp_path / "book.vg1")
        char(app, "/")
        for _ in range(4):
            press(app, "RIGHT")      # FILE
        press(app, "ENTER")
        press(app, "ENTER")          # SAVE
        type_text(app, path); press(app, "ENTER")
        assert os.path.exists(path)
        app2, win2 = make_app(filepath=path)
        assert app2.sheet.value_at(0, 2) == 579.0
        assert app2.sheet.raw_at(0, 2) == "@SUM(A1..A2)"
        assert app2.sheet.col_width(0) == 12
        assert app2.sheet.cell(0, 1).fmt == {"type": "fixed", "decimals": 1}


class TestDataFillSort:
    def test_acceptance_5_fill_and_sort(self, make_app):
        app, win = make_app()
        # /DATA FILL A1..A5 = 5, step -1, stop 1
        char(app, "/")
        for _ in range(7):
            press(app, "RIGHT")      # DATA
        press(app, "ENTER")
        press(app, "ENTER")          # FILL
        type_text(app, "A1..A5"); press(app, "ENTER")
        type_text(app, "5"); press(app, "ENTER")
        type_text(app, "-1"); press(app, "ENTER")
        type_text(app, "1"); press(app, "ENTER")
        assert [app.sheet.value_at(0, r) for r in range(5)] == [5.0, 4.0, 3.0, 2.0, 1.0]
        # /DATA SORT DATA-RANGE A1..A5
        char(app, "/")
        for _ in range(7):
            press(app, "RIGHT")      # DATA
        press(app, "ENTER")
        press(app, "DOWN"); press(app, "DOWN")   # SORT
        press(app, "ENTER")
        press(app, "ENTER")          # DATA-RANGE
        type_text(app, "A1..A5"); press(app, "ENTER")
        # /DATA SORT PRIMARY-KEY A1 ascending
        char(app, "/")
        for _ in range(7):
            press(app, "RIGHT")
        press(app, "ENTER")
        press(app, "DOWN"); press(app, "DOWN")
        press(app, "ENTER")
        press(app, "DOWN")           # PRIMARY-KEY
        press(app, "ENTER")
        type_text(app, "A1"); press(app, "ENTER")
        type_text(app, "YES"); press(app, "ENTER")
        # /DATA SORT GO
        char(app, "/")
        for _ in range(7):
            press(app, "RIGHT")
        press(app, "ENTER")
        press(app, "DOWN"); press(app, "DOWN")
        press(app, "ENTER")
        for _ in range(4):
            press(app, "DOWN")       # GO
        press(app, "ENTER")
        assert [app.sheet.value_at(0, r) for r in range(5)] == [1.0, 2.0, 3.0, 4.0, 5.0]


class TestGraph:
    def test_acceptance_6_graph_bar_view(self, make_app):
        app, win = make_app()
        for v in ("5", "3", "8"):
            type_text(app, v)
            press(app, "ENTER")
        char(app, "/")
        for _ in range(6):
            press(app, "RIGHT")      # GRAPH
        press(app, "ENTER")
        char(app, "a")               # series A
        type_text(app, "A1..A3"); press(app, "ENTER")
        type_text(app, "S"); press(app, "ENTER")
        char(app, "/")
        for _ in range(6):
            press(app, "RIGHT")
        press(app, "ENTER")
        press(app, "ENTER")          # TYPE
        press(app, "DOWN")
        press(app, "ENTER")          # BAR
        assert app.graph.type == "BAR"
        char(app, "/")
        for _ in range(6):
            press(app, "RIGHT")
        press(app, "ENTER")
        char(app, "v")               # VIEW
        assert app.mode == "GRAPH"
        snap = screen_text(app, win)
        assert "#" in snap or "█" in snap   # bars drawn (ASCII or UTF-8 glyph)
        press(app, "ENTER")          # any key returns
        assert app.mode == "READY"


class TestMouse:
    def test_menu_click_dropdown_executes(self, make_app):
        app, win = make_app()
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=2, y=0, button=0, press=True)))
        assert app.mode == "MENU"
        press(app, "ENTER")          # open WORKSHEET submenu
        assert app.menu.depth == 2
        # click STATUS (index 9 in WORKSHEET children) at box x=1, y=1+1+9=11
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=2, y=11, button=0, press=True)))
        assert app.mode == "READY"
        assert "cells=" in app.message

    def test_double_click_cell_edits(self, make_app):
        app, win = make_app()
        type_text(app, "42"); press(app, "ENTER")
        press(app, "UP")
        # cell A1 is at x=7..15, y=4
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=8, y=4, button=0, press=True)))
        assert app.cur == (0, 0)
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=8, y=4, button=0, press=True)))
        assert app.mode == "EDIT"
        assert app.entry == "42"

    def test_wheel_scrolls(self, make_app):
        app, win = make_app()
        for _ in range(30):
            press(app, "DOWN")
        assert app.top[1] > 0
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=10, y=10, button=64, press=True, wheel_up=True)))
        assert app.top[1] >= 0
        top_before = app.top[1]
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=10, y=10, button=65, press=True, wheel_down=True)))
        assert app.top[1] == top_before + 1

    def test_click_column_header_and_row(self, make_app):
        app, win = make_app()
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=7 + 9 + 2, y=3, button=0, press=True)))
        assert app.cur[0] == 1   # column B header
        app.handle(KeyEvent("mouse", mouse=MouseEvent(x=2, y=4 + 5, button=0, press=True)))
        assert app.cur[1] == 5


class TestModes:
    def test_ctrl_c_unwinds(self, make_app):
        app, win = make_app()
        char(app, "/")
        assert app.mode == "MENU"
        char(app, "\x03")
        assert app.mode == "READY"
        assert app.message == "CANCELED"

    def test_tilde_jumps_to_a1(self, make_app):
        app, win = make_app()
        press(app, "RIGHT"); press(app, "DOWN"); press(app, "DOWN")
        char(app, "~")
        assert app.cur == (0, 0)

    def test_f9_recalc(self, make_app):
        app, win = make_app()
        type_text(app, "2"); press(app, "ENTER")
        type_text(app, "+A1*A1"); press(app, "ENTER")
        assert app.sheet.value_at(0, 1) == 4.0
        press(app, "UP"); press(app, "UP")         # to A1
        type_text(app, "3"); press(app, "ENTER")   # A1 = 3, A2 recalcs to 9
        assert app.sheet.value_at(0, 1) == 9.0
        press(app, "F9")
        assert "Recalculated" in app.message
