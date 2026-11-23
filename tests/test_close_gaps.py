"""Gap-closing features: MEM indicator, mouse column resize, /RANGE INPUT,
multi-sheet (3-D) printing, 3-D ref narrowing on sheet delete, implicit
intersection, 3-D -> 2-D copy rejection."""

import pytest

from descartes.grid import split_range
from descartes.keys import MouseEvent
from descartes.screen import ROW_HEADERS, ROW_INDICATOR, GUTTER_W
from descartes.grid import DEFAULT_COL_WIDTH

from conftest import draw


def _header_boundary_x(app, col=0):
    pane = app.pane_layout()[0]
    return app.col_x(col, pane) + app.sheet.col_width(col) - 1


class TestMemIndicator:
    def test_mem_lights_for_large_workbook(self, make_app):
        app, win = make_app()
        for r in range(8000):
            app.sheet.set_entry(0, r, "'" + "x" * 70)
        assert app.mem_bytes() > 512 * 1024
        draw(app)
        line = win.text()[ROW_INDICATOR]
        assert "MEM" in line

    def test_mem_off_for_small_workbook(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        draw(app)
        line = win.text()[ROW_INDICATOR]
        assert "MEM" not in line


class TestMouseColumnResize:
    def test_drag_header_boundary_resizes(self, make_app):
        app, win = make_app()
        bx = _header_boundary_x(app, 0)
        app.handle_mouse(MouseEvent(bx, ROW_HEADERS, button=1))
        assert app._resize_col is not None
        app.handle_mouse(MouseEvent(bx + 4, ROW_HEADERS, button=1, press=False, drag=True))
        assert app.sheet.col_width(0) == DEFAULT_COL_WIDTH + 4
        app.handle_mouse(MouseEvent(bx + 4, ROW_HEADERS, button=1, press=False, release=True))
        assert app._resize_col is None
        assert app.sheet.col_width(0) == DEFAULT_COL_WIDTH + 4

    def test_plain_header_click_still_moves_cursor(self, make_app):
        app, win = make_app()
        x = app.col_x(2, app.pane_layout()[0]) + 1
        app.handle_mouse(MouseEvent(x, ROW_HEADERS, button=1))
        assert app._resize_col is None
        assert app.cur[0] == 2


class TestRangeInput:
    def test_range_input_stored(self, make_app):
        app, win = make_app()
        app.cmd_range_input(split_range("B2..C3"))
        assert app.input_range == ((1, 1), (2, 2))


class TestMultiSheetPrint:
    def test_print_3d_range_covers_all_sheets(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.cmd_insert_sheet()
        app.sheet.set_entry(0, 0, "2")
        out = tmp_path / "out.txt"
        app.cmd_print("A:B!A1..A1", dest="file:" + str(out))
        text = out.read_text()
        assert "Sheet A" in text and "Sheet B" in text
        assert text.index("Sheet A") < text.index("1") < text.index("Sheet B")

    def test_print_2d_range_unchanged(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "42")
        out = tmp_path / "out.txt"
        app.cmd_print("A1..A1", dest="file:" + str(out))
        text = out.read_text()
        assert "42" in text and "Sheet A" not in text


class TestDeleteSheetNarrows3DRefs:
    def test_range3d_narrows_and_vanishes(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.cmd_insert_sheet()          # C
        app.sheets["B"].set_entry(0, 0, "5")
        app.sheets["C"].set_entry(0, 0, "7")
        app.active = "A"
        app.sheet.set_entry(0, 0, "+@SUM(B:C!A1..A1)")
        app._register_all()
        app._recalc(force=True)
        assert app.sheet.value_at(0, 0) == 12.0
        app.cmd_delete_sheet("B")
        assert "C!A1" in app.sheets["A"].cells[(0, 0)].raw
        assert app.sheets["A"].value_at(0, 0) == 7.0
        app.cmd_delete_sheet("C")
        assert "@ERR()" in app.sheets["A"].cells[(0, 0)].raw

    def test_cell3d_collapses_to_2d_when_remaining_is_own_sheet(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.sheets["A"].set_entry(0, 0, "9")
        app.active = "A"
        app.sheet.set_entry(1, 0, "+A:B!A1")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(1, 0) == 9.0
        app.cmd_delete_sheet("B")
        assert app.sheets["A"].cells[(1, 0)].raw == "+A1"

    def test_cell3d_to_foreign_sheet_stays_qualified(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.cmd_insert_sheet()          # C
        app.sheets["C"].set_entry(0, 0, "4")
        app.active = "A"
        app.sheet.set_entry(0, 0, "+B:C!A1")
        app._register_all()
        app._recalc(force=True)
        app.cmd_delete_sheet("B")
        assert "C!A1" in app.sheets["A"].cells[(0, 0)].raw
        assert app.sheets["A"].value_at(0, 0) == 4.0

    def test_3d_name_narrows_on_delete(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.cmd_insert_sheet()          # C
        app.sheets["C"].set_entry(0, 0, "3")
        app.names["TOT"] = {"sheet": "A", "range": ((0, 0), (0, 0)),
                            "sheets": ["A", "C"]}
        app.active = "A"
        app.sheet.set_entry(1, 0, "+@SUM(TOT)")
        app._register_all()
        app._recalc(force=True)
        app.cmd_delete_sheet("B")
        assert app.names["TOT"]["sheets"] == ["A", "C"]
        app.cmd_delete_sheet("A")
        assert "sheets" not in app.names["TOT"]
        assert app.names["TOT"]["sheet"] == "C"


class TestSortRefRemap:
    def test_refs_into_sorted_band_follow_data(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "3")
        app.sheet.set_entry(1, 0, "+A1*10")
        app.sheet.set_entry(0, 1, "1")
        app.sheet.set_entry(1, 1, "+A2*10")
        app.sheet.set_entry(0, 2, "2")
        app.sheet.set_entry(1, 2, "+A3*10")
        app.sheet.set_entry(3, 0, "+A1")          # outside the sorted range
        app._register_all()
        app._recalc(force=True)
        app.sort_state.update(range=split_range("A1..B3"), keys=[(0, 0, True)])
        app.cmd_sort_go()
        assert app.sheet.value_at(0, 0) == 1.0
        assert app.sheet.value_at(0, 2) == 3.0
        assert app.sheet.value_at(1, 2) == 30.0   # B followed its row's A-ref
        assert app.sheet.value_at(3, 0) == 1.0    # D1 ref untouched


class TestQueryExtract:
    def test_extract_shifts_formula_refs(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "'h1")
        app.sheet.set_entry(1, 0, "'h2")
        app.sheet.set_entry(0, 1, "'x")
        app.sheet.set_entry(1, 1, "+A2*2")
        app.sheet.set_entry(0, 2, "'y")
        app.sheet.set_entry(1, 2, "5")
        app.sheet.set_entry(3, 0, "'h1")
        app.sheet.set_entry(3, 1, "'x")
        app._register_all()
        app._recalc(force=True)
        app.query_state.update(input=split_range("A1..B3"),
                               criterion=split_range("D1..D2"),
                               output=split_range("F1..G2"))
        app.cmd_query("extract")
        assert app.sheet.value_at(5, 1) == "x"
        g2 = app.sheet.cells[(6, 1)]
        assert g2.raw == "+E2*2"
