"""Multi-sheet workbooks: sheet switching, 3-D range syntax, cross-sheet recalc."""

import pytest

from conftest import FakeWindow, char, draw, press, type_text
from descartes.engine import (FormulaSyntaxError, adjust_refs, collect_xrefs,
                              parse, serialize, shift_raw)
from descartes.keys import read_event


class TestThreeDRangeSyntax:
    @pytest.mark.parametrize("raw,expect", [
        ("+@SUM(A:C!A1..B2)", "@SUM(A:C!A1..B2)"),
        ("+B!A1*2", "(B!A1*2)"),
        ("+$A:$C!$A$1", "A:C!$A$1"),
        ("+@SUM(A!A1..A!B2)", "@SUM(A!A1..B2)"),
        ("+A1..B2", "A1..B2"),
    ])
    def test_parse_serialize_roundtrip(self, raw, expect):
        assert serialize(parse(raw)) == expect
        parse(serialize(parse(raw)))

    @pytest.mark.parametrize("bad", ["+A:!A1", "+A!1", "+A:C", "+A:C!"])
    def test_bad_sheet_refs_rejected(self, bad):
        with pytest.raises(FormulaSyntaxError):
            parse(bad)

    def test_collect_xrefs(self):
        xrefs, xranges = collect_xrefs(parse("+@SUM(A:C!A1..B2)+B!A1"))
        assert ("B", "B", 0, 0) in xrefs
        assert ("A", "C", (0, 0), (1, 1)) in xranges

    def test_shift_preserves_sheet_span(self):
        assert shift_raw("+@SUM(A:C!A1..B2)+B!A1", 1, 2) == "+@SUM(A:C!B3..C4)+B!B3"

    def test_adjust_refs_preserves_sheet_span(self):
        assert serialize(adjust_refs(parse("+@SUM(A:C!A1..B2)"), "row", 0, 1)) == \
            "@SUM(A:C!A2..B3)"


class TestThreeDEval:
    def test_sum_across_sheets(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.cmd_insert_sheet()          # C
        app.sheets["A"].set_entry(0, 0, "1")
        app.sheets["B"].set_entry(0, 0, "2")
        app.sheets["C"].set_entry(0, 0, "3")
        app.switch_sheet(-1)            # A active? order A,B,C -> active C; go to A
        app.active = "A"
        app.sheet.set_entry(1, 0, "+@SUM(A:C!A1..A1)")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(1, 0) == 6.0

    def test_single_sheet_ref(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B active
        app.sheet.set_entry(0, 0, "5")
        app.switch_sheet(-1)            # A
        app.sheet.set_entry(0, 0, "+B!A1*2")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(0, 0) == 10.0

    def test_scalar_3d_uses_active_sheet_in_span(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.sheets["A"].set_entry(0, 0, "1")
        app.sheets["B"].set_entry(0, 0, "2")
        app.active = "A"
        app.sheet.set_entry(1, 0, "+A:B!A1")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(1, 0) == 1.0
        app.active = "B"
        app.sheet.set_entry(1, 0, "+A:B!A1")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["B"].value_at(1, 0) == 2.0

    def test_cross_sheet_recalc_on_edit(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B active
        app.sheet.set_entry(0, 0, "5")
        app.switch_sheet(-1)            # A
        app.sheet.set_entry(0, 0, "+B!A1*2")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(0, 0) == 10.0
        app.sheets["B"].set_entry(0, 0, "7")
        app._touch("B", (0, 0))
        app._recalc()
        assert app.sheets["A"].value_at(0, 0) == 14.0


class TestSheetSwitching:
    def test_ctrl_pgdn_pgup_wrap(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        assert app.active == "B"
        press(app, "CTRL_PGDN")         # wraps to A
        assert app.active == "A"
        press(app, "CTRL_PGUP")         # wraps to B
        assert app.active == "B"

    def test_raw_ctrl_pgdn_sequence(self, fake_curses):
        win = FakeWindow()
        win.keys = [27, ord("["), ord("6"), ord(";"), ord("5"), ord("~")]
        ev = read_event(win, escdelay_ms=5)
        assert ev.kind == "key" and ev.name == "CTRL_PGDN", ev

    def test_control_panel_sheet_qualified_when_multi(self, make_app):
        app, win = make_app()
        draw(app)
        assert win.text()[1].startswith("A1: READY")
        app.cmd_insert_sheet()
        draw(app)
        assert win.text()[1].startswith("B!A1: READY")

    def test_insert_delete_sheet(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()
        app.cmd_insert_sheet()
        assert app.sheet_order == ["A", "B", "C"]
        assert app.active == "C"
        app.cmd_delete_sheet("b")
        assert app.sheet_order == ["A", "C"]
        assert app.active == "C"
        app.cmd_delete_sheet("C")
        assert app.sheet_order == ["A"]
        assert app.active == "A"
        app.cmd_delete_sheet("A")
        assert app.error != ""
        assert app.sheet_order == ["A"]

    def test_menu_has_sheet_items(self):
        from descartes.menus import MENU_TREE
        ws = next(m for m in MENU_TREE if m.label == "WORKSHEET")
        ins = next(m for m in ws.children if m.label == "INSERT")
        dele = next(m for m in ws.children if m.label == "DELETE")
        assert any(m.label == "SHEET" for m in ins.children)
        assert any(m.label == "SHEET" for m in dele.children)


class TestMultiSheetPersistence:
    def test_save_load_roundtrip_multi_sheet(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.cmd_insert_sheet()          # B
        app.sheet.set_entry(0, 0, "+A!A1*10")
        app.sheet.set_entry(1, 0, "hello")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["B"].value_at(0, 0) == 10.0
        path = str(tmp_path / "multi.vg1")
        app.cmd_save(path)
        app2, win2 = make_app()
        app2.load_file(path)
        assert app2.sheet_order == ["A", "B"]
        assert app2.active == "B"
        assert app2.sheets["A"].value_at(0, 0) == 1.0
        assert app2.sheets["B"].value_at(0, 0) == 10.0
        assert app2.sheets["B"].value_at(1, 0) == "hello"
        assert app2.sheets["B"].raw_at(0, 0) == "+A!A1*10"


class TestRangeNamesInFormulas:
    def test_parse_serialize_name(self):
        from descartes.engine import parse, serialize
        assert serialize(parse("+TOTAL*2")) == "(TOTAL*2)"
        assert serialize(parse("+@SUM(ALL3D)")) == "@SUM(ALL3D)"

    def test_name_not_shifted_on_copy(self):
        assert shift_raw("+@SUM(TOTAL)+A1", 1, 1) == "+@SUM(TOTAL)+B2"

    def test_name_validation(self):
        from descartes.grid import is_valid_name
        assert is_valid_name("TOTAL")
        assert is_valid_name("Q_1")
        assert not is_valid_name("A1")
        assert not is_valid_name("IV8192")
        assert not is_valid_name("A!A1")
        assert not is_valid_name("1X")
        assert not is_valid_name("")

    def test_unknown_name_is_err(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "+NOSUCHNAME")
        app._register_all()
        app._recalc(force=True)
        assert getattr(app.sheet.value_at(0, 0), "_is_lotus_error", False)

    def test_sum_over_2d_name_same_sheet(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "2")
        app.cmd_name_create("TOTAL", "A1..A2")
        app.sheet.set_entry(1, 0, "+@SUM(TOTAL)")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(1, 0) == 3.0

    def test_name_edit_propagates(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "2")
        app.cmd_name_create("TOTAL", "A1..A2")
        app.sheet.set_entry(1, 0, "+@SUM(TOTAL)")
        app._register_all()
        app._recalc(force=True)
        app.sheet.set_entry(0, 1, "5")
        app._touch("A", (0, 1))
        app._recalc()
        assert app.sheets["A"].value_at(1, 0) == 6.0

    def test_cross_sheet_name(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()          # B
        app.sheets["B"].set_entry(0, 0, "10")
        app.sheets["B"].set_entry(0, 1, "20")
        app.cmd_name_create("BVALS", "B!A1..A2")
        app.active = "A"
        app.sheet.set_entry(0, 0, "+@SUM(BVALS)")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["A"].value_at(0, 0) == 30.0

    def test_3d_name_sum(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.cmd_insert_sheet()
        app.cmd_insert_sheet()
        app.sheets["B"].set_entry(0, 0, "10")
        app.sheets["C"].set_entry(0, 0, "100")
        app.cmd_name_create("ALL3D", "A:C!A1..A1")
        app.active = "B"
        app.sheet.set_entry(1, 0, "+@SUM(ALL3D)")
        app._register_all()
        app._recalc(force=True)
        assert app.sheets["B"].value_at(1, 0) == 111.0

    def test_name_create_rejects_cell_like(self, make_app):
        app, win = make_app()
        app.cmd_name_create("A1", "A1..A2")
        assert app.error != ""
        assert "A1" not in app.names

    def test_prompt_accepts_3d_range_text(self, make_app):
        app, win = make_app()
        from descartes.app import Prompt
        p = Prompt([{"text": "r", "kind": "range"}], lambda a: None)
        ok, err = p.validate("A:C!A1..B2")
        assert ok, err
        ok, err = p.validate("B!A1..B2")
        assert ok, err
        ok, err = p.validate("bogus")
        assert not ok

    def test_name_create_3d_stored_and_saved(self, make_app, tmp_path):
        app, win = make_app()
        app.cmd_insert_sheet()
        app.cmd_name_create("SPAN", "A:B!A1..B2")
        assert app.names["SPAN"]["sheets"] == ["A", "B"]
        path = str(tmp_path / "n.vg1")
        app.cmd_save(path)
        app2, win2 = make_app()
        app2.load_file(path)
        assert app2.names["SPAN"]["sheets"] == ["A", "B"]
        assert app2.names["SPAN"]["range"] == ((0, 0), (1, 1))


class TestCrossSheetCopyMove:
    def test_copy_sheet_qualified_source(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()                      # B active
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(0, 1, "+A1*2")
        app._register_all()
        app._recalc(force=True)
        app.active = "A"
        app.cmd_copy(["B!A1..A2", "C1"], move=False)
        assert app.sheets["A"].value_at(2, 0) == 5.0
        assert app.sheets["A"].value_at(2, 1) == 10.0
        assert "C1" in app.sheets["A"].raw_at(2, 1)

    def test_copy_3d_source_to_3d_dest(self, make_app):
        app, win = make_app()
        app.sheets["A"].set_entry(0, 0, "1")
        app.cmd_insert_sheet()                      # B
        app.sheet.set_entry(0, 0, "2")
        app.cmd_copy(["A:B!A1..A1", "A:B!C1..C1"], move=False)
        assert app.sheets["A"].value_at(2, 0) == 1.0
        assert app.sheets["B"].value_at(2, 0) == 2.0

    def test_copy_3d_source_to_2d_dest_is_error(self, make_app):
        app, win = make_app()
        app.sheets["A"].set_entry(0, 0, "1")
        app.cmd_insert_sheet()                      # B
        app.sheet.set_entry(0, 0, "2")
        app.cmd_copy(["A:B!A1..A1", "C1"], move=False)   # 2-D dst: rejected
        assert "3-D" in app.error
        assert app.sheets["B"].value_at(2, 0) is None

    def test_copy_single_sheet_3d_source_to_2d_dest(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()                      # B (active)
        app.sheet.set_entry(0, 0, "2")
        app.cmd_copy(["B!A1..A1", "C1"], move=False)
        assert app.sheets["B"].value_at(2, 0) == 2.0

    def test_move_cross_sheet_clears_source(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()                      # B
        app.sheet.set_entry(0, 0, "7")
        app.active = "A"
        app.cmd_copy(["B!A1", "C1"], move=True)
        assert app.sheets["B"].cell(0, 0) is None
        assert app.sheets["A"].value_at(2, 0) == 7.0

    def test_3d_source_2d_dest_single_sheet_ok(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()
        app.sheet.set_entry(0, 0, "9")
        app.active = "A"
        app.cmd_copy(["B!A1", "C1"], move=False)
        assert app.sheets["A"].value_at(2, 0) == 9.0


class TestRangeJustify:
    def test_justify_rotates_row_right(self, make_app):
        app, win = make_app()
        for c, v in enumerate(("a", "b", "c")):
            app.sheet.set_entry(c, 0, v)
        app.cmd_range_justify(((0, 0), (2, 0)))
        assert [app.sheet.value_at(c, 0) for c in range(3)] == ["c", "a", "b"]

    def test_justify_shifts_formula_refs(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "5")
        app.sheet.set_entry(1, 0, "+A1*2")
        app._register_all()
        app._recalc(force=True)
        app.cmd_range_justify(((0, 0), (1, 0)))
        # old B1 (+A1*2) rotated into A1 -> shifted to +B1*2, now pointing at the moved 5
        assert app.sheet.raw_at(0, 0).startswith("+")
        assert "B1" in app.sheet.raw_at(0, 0)
        assert app.sheet.value_at(0, 0) == 10.0


class TestGlobalFormat:
    def test_global_format_applies(self, make_app):
        from descartes import fmt
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1234.5")
        app._actions["wg_format"](["FIXED"])
        assert app.sheet.global_fmt == {"type": "fixed", "decimals": 2}
        assert fmt.display(app.sheet.value_at(0, 0), app.sheet.global_fmt) == "1234.50"

    def test_global_format_general_clears(self, make_app):
        app, win = make_app()
        app._actions["wg_format"](["PERCENT"])
        assert app.sheet.global_fmt == {"type": "percent", "decimals": 0}
        app._actions["wg_format"](["GENERAL"])
        assert app.sheet.global_fmt is None


class TestBacksolve:
    def test_linear(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "2")
        app.sheet.set_entry(1, 0, "+A1*3")
        app._register_all()
        app._recalc(force=True)
        app.cmd_backsolve("B1", "30", "A1")
        assert app.error == ""
        assert abs(app.sheet.value_at(0, 0) - 10.0) < 1e-9
        assert app.sheet.value_at(1, 0) == 30.0

    def test_quadratic(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "3")
        app.sheet.set_entry(1, 0, "+A1^2")
        app._register_all()
        app._recalc(force=True)
        app.cmd_backsolve("B1", "16", "A1")
        assert app.error == ""
        assert abs(app.sheet.value_at(1, 0) - 16.0) < 1e-6
        assert abs(abs(app.sheet.value_at(0, 0)) - 4.0) < 1e-6

    def test_no_dependency_reports_error(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(2, 0, "5")
        app.sheet.set_entry(1, 0, "+C1")
        app._register_all()
        app._recalc(force=True)
        app.cmd_backsolve("B1", "99", "A1")
        assert app.error != ""
        assert app.sheet.cell(0, 0) is None   # variable cell restored on failure

    def test_non_numeric_target_reports_error(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(1, 0, "hello")
        app.cmd_backsolve("B1", "5", "A1")
        assert "not numeric" in app.error

    def test_menu_entry_exists(self):
        from descartes.menus import MENU_TREE
        rng = next(m for m in MENU_TREE if m.label == "RANGE")
        assert any(m.label == "BACKSOLVE" for m in rng.children)


class TestGraphGridAndPrintRange:
    def _bar_graph(self, app):
        from descartes.graph import GraphDef
        app.graph = GraphDef("BAR", series=[{"name": "S", "range": ((0, 0), (0, 2))}])
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "3")
        app.sheet.set_entry(0, 2, "2")

    def test_grid_toggle(self, make_app):
        from descartes import graph as g
        app, win = make_app()
        self._bar_graph(app)
        with_grid = g.render(app.graph, app.sheet, 60, 18)
        app._actions["g_opt_grid"]([])
        assert app.graph.options["grid"] is False
        without = g.render(app.graph, app.sheet, 60, 18)
        dashes_with = sum(ln.count("-") for ln in with_grid)
        dashes_without = sum(ln.count("-") for ln in without)
        assert dashes_with > dashes_without > 0   # axis line stays, gridlines gone
        app._actions["g_opt_grid"]([])
        assert g.render(app.graph, app.sheet, 60, 18) == with_grid

    def test_print_range_to_named_file(self, make_app, tmp_path):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "42")
        out = str(tmp_path / "report.txt")
        app._actions["p_range"](["A1", out])
        assert "42" in open(out).read()

    def test_print_range_printer_default(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "7")
        app._actions["p_range"](["A1", "PRINTER"])
        assert "Sent to printer" in app.message


class TestNameLabelsAllPositions:
    def test_left_and_bottom(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "10")
        app.sheet.set_entry(1, 0, "20")
        app.sheet.set_entry(2, 0, "ROW1")     # rightmost label, row 0
        app.sheet.set_entry(0, 1, "30")
        app.sheet.set_entry(1, 1, "40")
        app.sheet.set_entry(2, 1, "ROW2")
        app.sheet.set_entry(0, 2, "COLA")
        app.sheet.set_entry(1, 2, "COLB")
        app.cmd_name_labels(((0, 0), (2, 2)), "LEFT")
        assert app.names["ROW1"]["range"] == ((0, 0), (1, 0))
        app.cmd_name_labels(((0, 0), (2, 2)), "BOTTOM")
        assert app.names["COLA"]["range"] == ((0, 0), (0, 1))
        assert app.names["COLB"]["range"] == ((1, 0), (1, 1))
