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


# ---------------------------------------------------------------------------
# WK3 multi-sheet import
# ---------------------------------------------------------------------------

import struct as _struct

from descartes import persist as _persist


def _rec(op, payload=b""):
    return _struct.pack("<HH", op, len(payload)) + payload


def _bof3():
    return _struct.pack("<HH", 0x0000, 26) + _struct.pack("<HH", 0x1000, 0x0004) + b"\0" * 22


def _eof():
    return _struct.pack("<HH", 0x0001, 0)


def _label3(tab, col, row, prefix, text):
    return _rec(0x16, _struct.pack("<HHH", col, row, tab) + prefix.encode("cp437")
                + text.encode("cp437") + b"\0")


def _number3(tab, col, row, v):
    return _rec(0x17, _struct.pack("<HHH", col, row, tab) + _struct.pack("<d", v))


def _smallnum3(tab, col, row, h):
    return _rec(0x18, _struct.pack("<HHH", col, row, tab) + _struct.pack("<h", h))


def _formula3(tab, col, row, cached, tokens):
    return _rec(0x19, _struct.pack("<HHH", col, row, tab)
                + _struct.pack("<dH", cached, len(tokens)) + tokens)


def _colw3(tab, pairs):
    payload = bytes([tab, 0, 0, 0]) + b"".join(bytes([c, w]) for c, w in pairs)
    return _rec(0x07, payload)


def _userrange(name, c1, r1, c2, r2, tabs=None):
    payload = _struct.pack("<H", 0) + name.encode("cp437").ljust(16, b"\0") \
        + _struct.pack("<HHHH", c1, r1, c2, r2)
    if tabs:
        payload += _struct.pack("<HH", *tabs)
    return _rec(0x09, payload)


def _namedsheet(tab, name):
    return _rec(0x1B, _struct.pack("<HH", 14000, tab) + name.encode("cp437") + b"\0")


def _cref3(col, row, tab):
    return b"\x02\x00" + _struct.pack("<HBB", row, tab, col)


def _build_wk3(path):
    rpn_sum_b = _cref3(1, 0, 0) + _cref3(1, 1, 0) + b"\x10\x0a"
    rpn_cross = _cref3(1, 0, 0) + _cref3(1, 1, 0) + b"\x10\x0a"
    data = b"".join([
        _bof3(),
        _namedsheet(0, "Data"),
        _colw3(0, [(0, 12)]),
        _label3(0, 0, 0, "'", "Item"),
        _number3(0, 1, 0, 10.0),
        _number3(0, 1, 1, 20.0),
        _smallnum3(0, 0, 1, 6),
        _formula3(0, 0, 2, 30.0, rpn_sum_b),
        _userrange("PRICES", 1, 0, 1, 1),
        _eof(),
        _bof3(),
        _namedsheet(1, "Calc"),
        _label3(1, 0, 0, "'", "Total"),
        _formula3(1, 1, 0, 30.0, rpn_cross),
        _userrange("ALL", 1, 0, 1, 1, tabs=(0, 1)),
        _eof(),
    ])
    path.write_bytes(data)


class TestWk3Read:
    def test_all_sheets_read(self, tmp_path):
        p = tmp_path / "book.wk3"
        _build_wk3(p)
        data = _persist.import_file(str(p))
        assert list(data["sheets"]) == ["DATA", "CALC"]
        ws = data["sheets"]["DATA"]
        assert ws.cell(0, 0).value == "Item"
        assert ws.cell(1, 0).value == 10.0
        assert ws.cell(1, 1).value == 20.0
        assert ws.cell(0, 1).value == 3.0          # smallnum h=6 -> 3
        assert ws.widths.get(0) == 12
        assert data["active"] == "DATA"

    def test_formulas_and_names(self, tmp_path):
        p = tmp_path / "book.wk3"
        _build_wk3(p)
        data = _persist.import_file(str(p))
        raw_a = data["sheets"]["DATA"].cell(0, 2).raw
        assert raw_a == "+(B1+B2)"
        raw_b = data["sheets"]["CALC"].cell(1, 0).raw
        assert raw_b == "+(DATA!B1+DATA!B2)"
        assert data["names"]["PRICES"]["sheet"] == "DATA"
        assert data["names"]["ALL"]["sheets"] == ["DATA", "CALC"]
        eng = _persist.attach_engine(data["sheets"]["DATA"])
        assert data["sheets"]["DATA"].value_at(0, 2) == 30.0

    def test_wk3_formula_cross_sheet_evaluates(self, make_app, tmp_path):
        p = tmp_path / "book.wk3"
        _build_wk3(p)
        app, win = make_app()
        app.load_file(str(p))
        assert list(app.sheets) == ["DATA", "CALC"]
        assert app.sheets["CALC"].value_at(1, 0) == 30.0
        assert app.sheets["DATA"].value_at(0, 2) == 30.0
        assert app.names["ALL"]["sheets"] == ["DATA", "CALC"]

    def test_password_rejected(self, tmp_path):
        p = tmp_path / "prot.wk3"
        p.write_bytes(_bof3() + _rec(0x0002, b"\x01" * 8) + _eof())
        with pytest.raises(ValueError):
            _persist.import_file(str(p))

    def test_x87_and_3d_tokens(self):
        mant = int(2.5 * (1 << 62))
        x87 = mant.to_bytes(8, "little") + _struct.pack("<H", 16384)
        toks = b"\x00" + x87 + b"\x0a"
        text, ok = _persist._rpn3_to_infix(toks, 0, 0, 0, ["A"])
        assert ok and float(text) == 2.5
        rref = b"\x03\x00" + _struct.pack("<HBB", 0, 0, 0) + _struct.pack("<HBB", 1, 1, 1) + b"\x0a"
        text, ok = _persist._rpn3_to_infix(rref, 0, 0, 0, ["A", "C"])
        assert ok and text == "A:C!A1..B2"

    def test_wk1_still_single_sheet(self, tmp_path):
        p = tmp_path / "old.wk1"
        p.write_bytes(_struct.pack("<HHH", 0x0000, 2, 0x0406)
                      + _rec(0x000E, _struct.pack("<BHHd", 0, 0, 0, 5.0))
                      + _struct.pack("<HH", 1, 0))
        data = _persist.import_file(str(p))
        assert list(data["sheets"]) == ["A"]
        assert data["sheets"]["A"].value_at(0, 0) == 5.0
