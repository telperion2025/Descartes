"""Regression tests for the peer-review findings."""

import pytest

from conftest import FakeWindow, char, draw, press, screen_text, type_text
from descartes import persist
from descartes.data_cmd import frequency
from descartes.engine import Engine
from descartes.grid import Worksheet, parse_address, split_range
from descartes.keys import KeyEvent, read_event


class TestLoneEsc:
    def test_lone_esc_is_esc_key_not_char(self, fake_curses):
        win = FakeWindow()
        win.keys = [27]          # ESC, then nothing (getch -> -1)
        ev = read_event(win, escdelay_ms=5)
        assert ev.kind == "key" and ev.name == "ESC", ev

    def test_esc_cancels_entry_not_appends(self, make_app):
        app, win = make_app()
        type_text(app, "12")
        press(app, "ESC")
        assert app.mode == "READY"
        assert app.entry == ""
        assert app.sheet.cell(0, 0) is None


class TestSaveErrorValues:
    def test_vg1_roundtrip_with_err(self, tmp_path):
        ws = Worksheet()
        ws.set_entry(0, 0, "1/0")
        ws.set_entry(1, 0, "@NA")
        ws.set_entry(2, 0, "5")
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        path = str(tmp_path / "e.vg1")
        persist.save_vg1(path, {"A": ws})
        data = persist.load_vg1(path)
        ws2 = data["sheets"]["A"]
        from descartes.functions import Error
        assert isinstance(ws2.value_at(0, 0), Error) and ws2.value_at(0, 0).code == "ERR"
        assert isinstance(ws2.value_at(1, 0), Error) and ws2.value_at(1, 0).code == "NA"
        assert ws2.value_at(2, 0) == 5.0
        assert ws2.raw_at(0, 0) == "1/0"


class TestSplitWindows:
    def test_horizontal_split_renders_two_panes(self, make_app):
        app, win = make_app()
        type_text(app, "42")
        press(app, "ENTER")
        app.set_split("H")
        snap = screen_text(app, win)
        lines = snap.split("\n")
        # pane 1 grid rows 4..11, pane 2 header at row 12, grid 13..21
        assert lines[4].startswith("     1|")
        assert lines[12].lstrip().startswith("A") or "A" in lines[12][7:16]
        assert lines[13].startswith("     1|")
        assert "42" in lines[4]

    def test_titles_frozen(self, make_app):
        app, win = make_app()
        for i, v in enumerate(["h1", "h2", "1", "2", "3"]):
            type_text(app, v)
            press(app, "ENTER")
        app.set_titles((0, 1))     # freeze row 1
        for _ in range(10):
            press(app, "DOWN")
        snap = screen_text(app, win)
        lines = snap.split("\n")
        # title row 1 stays pinned at the first grid row even when scrolled
        assert lines[4].startswith("     1|") and "h1" in lines[4]


class TestCtrlArrows:
    def test_ctrl_right_jumps_to_data_edge(self, make_app):
        app, win = make_app()
        type_text(app, "1"); press(app, "ENTER")     # A1 = 1
        press(app, "UP"); press(app, "RIGHT")        # B1
        type_text(app, "2"); press(app, "ENTER")     # B1 = 2
        app.cur = (0, 0)
        press(app, "CTRL_RIGHT")
        assert app.cur[0] == 2   # one past the last used column (B) -> C
        press(app, "CTRL_LEFT")
        assert app.cur[0] == 0


class TestCellFunction:
    def test_cell_attrs(self):
        ws = Worksheet()
        ws.set_entry(1, 2, "'hello")
        ws.set_entry(0, 0, '@CELL("ADDRESS",B3)')
        ws.set_entry(1, 0, '@CELL("TYPE",B3)')
        ws.set_entry(2, 0, '@CELL("ROW",B3)')
        ws.set_entry(3, 0, '@CELL("COL",B3)')
        ws.set_entry(4, 0, '@CELL("PREFIX",B3)')
        ws.set_entry(5, 0, '@CELL("WIDTH",B3)')
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        assert ws.value_at(0, 0) == "B3"
        assert ws.value_at(1, 0) == "label"
        assert ws.value_at(2, 0) == 3.0
        assert ws.value_at(3, 0) == "B"
        assert ws.value_at(4, 0) == "'"
        assert ws.value_at(5, 0) == 9.0


class TestFrequencyHorizontal:
    def test_horizontal_bins(self):
        ws = Worksheet()
        for addr, raw in {"A1": "1", "A2": "2", "A3": "3", "A4": "4", "C1": "2", "D1": "4"}.items():
            c, r = parse_address(addr)
            ws.set_entry(c, r, raw)
        eng = Engine(ws)
        eng.register_all()
        eng.recalc(force=True)
        counts = frequency(ws, eng, split_range("A1..A4"), split_range("C1..D1"))
        assert counts == [2, 2, 0]
        assert ws.value_at(2, 1) == 2.0   # row below the bin row
        assert ws.value_at(3, 1) == 2.0
        assert ws.value_at(4, 1) == 0.0


class TestFileQuit:
    def test_file_quit_exits(self, make_app):
        app, win = make_app()
        char(app, "/")
        for _ in range(4):
            press(app, "RIGHT")      # FILE
        press(app, "ENTER")
        char(app, "q")               # QUIT
        assert app.running is False


class TestColorScheme:
    def test_pairs_match_lotus_look(self, make_app):
        import curses
        app, win = make_app()
        a = app.screen.attrs
        # pair 1 white/black worksheet bg, 2 white/blue menu bar, 7 black/white cursor
        assert curses._pairs[1] == (curses.COLOR_WHITE, curses.COLOR_BLACK)
        assert curses._pairs[2] == (curses.COLOR_WHITE, curses.COLOR_BLUE)
        assert curses._pairs[7] == (curses.COLOR_BLACK, curses.COLOR_WHITE)
        assert curses._pairs[8] == (curses.COLOR_BLACK, curses.COLOR_CYAN)
        assert a[1] == curses.color_pair(1)
        assert a[7] & curses.A_BOLD  # cursor cell bold reverse

    def test_cursor_cell_reverse_video_attr(self, make_app):
        import curses
        app, win = make_app()
        type_text(app, "5")
        press(app, "ENTER")
        press(app, "UP")   # cursor back on A1
        draw(app)
        # A1 cell text at row 4, x=7..15 must carry the cursor attr (pair 7)
        attr = win.attrs[4][8]
        assert attr == app.screen.a(7), attr
        # worksheet background elsewhere is pair 1
        assert win.attrs[4][20] == app.screen.a(1)
        # control panel row is pair 8 (cyan panel)
        assert win.attrs[1][0] == app.screen.a(8)
        # READY menu bar is the blue pair 2
        assert win.attrs[0][0] == app.screen.a(2)
