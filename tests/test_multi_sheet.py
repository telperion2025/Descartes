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
