"""1-2-3 style {…} macros: tokenizer, runner commands, Alt+letter wiring."""

import pytest

from conftest import make_app  # noqa: F401  (fixture import for clarity)
from descartes.keys import KeyEvent
from descartes.macro import MacroError, MacroRunner, split_args, tokenize_macro


class TestTokenizer:
    def test_mixed_tokens(self):
        toks = tokenize_macro('{GOTO A1}12{ENTER}{LEFT}')
        assert toks == [("cmd", "GOTO", "A1"), ("keys", "12"), ("key", "ENTER"),
                        ("key", "LEFT")]

    def test_unterminated_brace(self):
        with pytest.raises(MacroError):
            tokenize_macro("{GOTO A1")

    def test_unknown_command(self):
        with pytest.raises(MacroError):
            tokenize_macro("{FOOBAR x}")

    def test_split_args_parens_and_quotes(self):
        assert split_args('B1,@SUM(A1,A2)') == ["B1", "@SUM(A1,A2)"]
        assert split_args('B1,"a,b"') == ["B1", '"a,b"']


def _run(app, raw, key=(3, 0)):   # scratch cell D1; macros must start with '{'
    app.sheet.set_entry(*key, raw)
    MacroRunner(app).run_cell(key)


class TestMacroCommands:
    def test_keystrokes_enter_data(self, make_app):
        app, win = make_app()
        _run(app, "{LOOK}12{ENTER}")
        assert app.sheet.value_at(0, 0) == 12.0
        assert app.cur == (0, 1)

    def test_goto_and_let(self, make_app):
        app, win = make_app()
        _run(app, "{GOTO B3}{LET B3, 5+6}")
        assert app.cur == (1, 2)
        assert app.sheet.value_at(1, 2) == 11.0

    def test_let_formula(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "3")
        _run(app, "{LET B1, @SUM(A1,A1)}")
        app._recalc(force=True)
        assert app.sheet.value_at(1, 0) == 6.0

    def test_if_else_end(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(1, 0, "5")
        _run(app, "{IF B1=5}{LET A1, 1}{ELSE}{LET A1, 2}{END}")
        assert app.sheet.value_at(0, 0) == 1.0
        app.sheet.set_entry(1, 0, "6")
        app._recalc(force=True)
        _run(app, "{IF B1=5}{LET A1, 1}{ELSE}{LET A1, 2}{END}")
        assert app.sheet.value_at(0, 0) == 2.0

    def test_nested_if(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(1, 0, "1")
        _run(app, "{IF B1=1}{IF B1>0}{LET A1, 7}{END}{END}")
        assert app.sheet.value_at(0, 0) == 7.0

    def test_call_return(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{CALL B1}{LET C1, 9}")
        app.sheet.set_entry(1, 0, "{LET B2, 8}{RETURN}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(1, 1) == 8.0
        assert app.sheet.value_at(2, 0) == 9.0

    def test_branch(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{BRANCH B1}{LET A1, 1}")
        app.sheet.set_entry(1, 0, "{LET A2, 5}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(0, 1) == 5.0
        assert app.sheet.cell(3, 0).raw.startswith("{")

    def test_halt(self, make_app):
        app, win = make_app()
        _run(app, "{LET A1, 1}{HALT}{LET A1, 2}")
        assert app.sheet.value_at(0, 0) == 1.0

    def test_recalc(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "4")
        app.sheet.set_entry(1, 0, "+A1*2")
        app.engine.register_all()
        _run(app, "{RECALC}")
        assert app.sheet.value_at(1, 0) == 8.0

    def test_step_limit_stops_runaway(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{BRANCH D1}")
        with pytest.raises(MacroError):
            MacroRunner(app, max_steps=500).run_cell((3, 0))
        assert app.running

    def test_unknown_target(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{GOTO Z9}{BRANCH B5}")
        with pytest.raises(MacroError):
            MacroRunner(app).run_cell((3, 0))

    def test_not_a_macro(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "hello")
        with pytest.raises(MacroError):
            MacroRunner(app).run_cell((3, 0))


class TestAltLetterWiring:
    def test_alt_letter_runs_named_macro(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "{LET B1, 42}")
        app.names["M"] = {"sheet": "A", "range": ((0, 0), (0, 0))}
        app.handle(KeyEvent("char", ch="m", name="ALT+m"))
        assert app.sheet.value_at(1, 0) == 42.0
        assert app.error == ""

    def test_alt_letter_without_name_is_plain_char(self, make_app):
        app, win = make_app()
        app.handle(KeyEvent("char", ch="z", name="ALT+z"))
        assert app.mode == "LABEL"   # fell through to normal entry

    def test_bad_macro_reports_error(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "{BRANCH B9}")
        app.names["K"] = {"sheet": "A", "range": ((0, 0), (0, 0))}
        app.handle(KeyEvent("char", ch="k", name="ALT+k"))
        assert app.error.startswith("Macro:")
        assert app.running


class TestInputCommands:
    def test_input_suspends_then_resumes_on_enter(self, make_app):
        app, win = make_app()
        app.input_range = ((0, 0), (0, 0))
        app.sheet.set_entry(3, 0, "{INPUT}{LET B1, 99}")
        MacroRunner(app).run_cell((3, 0))
        assert app.macro_wait is not None and app.macro_wait["kind"] == "input"
        assert app.mode == "EDIT"
        app.handle(KeyEvent("char", ch="7"))
        app.handle(KeyEvent("key", name="ENTER"))
        assert app.macro_wait is None
        assert app.sheet.value_at(0, 0) == 7.0
        assert app.sheet.value_at(1, 0) == 99.0

    def test_input_requires_range_input(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{INPUT}")
        with pytest.raises(MacroError):
            MacroRunner(app).run_cell((3, 0))

    def test_getnumber_prompts_and_stores(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, '{GETNUMBER "Qty?", B1}{LET C1, 1}')
        MacroRunner(app).run_cell((3, 0))
        assert app.macro_wait is not None and app.macro_wait["kind"] == "number"
        assert app.mode == "DIALOG"
        app.handle(KeyEvent("char", ch="4"))
        app.handle(KeyEvent("char", ch="2"))
        app.handle(KeyEvent("key", name="ENTER"))
        assert app.macro_wait is None
        assert app.sheet.value_at(1, 0) == 42.0
        assert app.sheet.value_at(2, 0) == 1.0

    def test_getlabel_prompts_and_stores_text(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, '{GETLABEL "Name?", B1}{LET C1, 2}')
        MacroRunner(app).run_cell((3, 0))
        assert app.macro_wait["kind"] == "text"
        app.handle(KeyEvent("char", ch="h"))
        app.handle(KeyEvent("char", ch="i"))
        app.handle(KeyEvent("key", name="ENTER"))
        assert app.sheet.value_at(1, 0) == "hi"
        assert app.sheet.value_at(2, 0) == 2.0

    def test_menubranch_feeds_keystrokes(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(4, 0, "'/W")
        app.sheet.set_entry(3, 0, "{MENUBRANCH E1}")
        MacroRunner(app).run_cell((3, 0))
        assert app.mode == "MENU"
        app.unwind("CANCELED")

    def test_define_creates_range_name(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "1")
        app.sheet.set_entry(0, 1, "2")
        app.sheet.set_entry(0, 2, "3")
        app.sheet.set_entry(3, 0, "{DEFINE TOTAL: A1..A3}{LET B1, @SUM(TOTAL)}")
        MacroRunner(app).run_cell((3, 0))
        app._recalc(force=True)
        assert app.names["TOTAL"]["range"] == ((0, 0), (0, 2))
        assert app.sheet.value_at(1, 0) == 6.0

    def test_define_3d_name(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()
        app.sheet.set_entry(3, 0, "{DEFINE ALL: A:B!A1..A1}")
        MacroRunner(app).run_cell((3, 0))
        assert app.names["ALL"].get("sheets") == ["A", "B"]


class TestForLoop:
    def test_for_loop(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{FOR A1, 1, 3}{LET B1, +A1}{FOREND}{LET C1, 99}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(0, 0) == 3.0
        assert app.sheet.value_at(1, 0) == 3.0
        assert app.sheet.value_at(2, 0) == 99.0

    def test_for_step_down_and_forbreak(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{FOR A1, 5, 1, -1}{IF A1=2}{FORBREAK}{END}{LET B1, +A1}{FOREND}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(0, 0) == 2.0
        assert app.sheet.value_at(1, 0) == 2.0   # B1=+A1 is a formula; follows the counter

    def test_nested_for(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{FOR A1, 1, 2}{FOR B1, 1, 2}{LET C1, +A1*10+B1}{FOREND}{FOREND}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(0, 0) == 2.0
        assert app.sheet.value_at(1, 0) == 2.0
        assert app.sheet.value_at(2, 0) == 22.0

    def test_for_without_forend_errors(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(3, 0, "{FOR A1, 1, 2}{LET B1, 1}")
        with pytest.raises(MacroError):
            MacroRunner(app).run_cell((3, 0))


class TestReviewFixes:
    def test_literal_braces(self):
        toks = tokenize_macro("{{}x{}}")
        assert "".join(t[1] for t in toks if t[0] == "keys") == "{x}"

    def test_bad_address_in_macro_reports_error_not_crash(self, make_app):
        app, win = make_app()
        app.sheet.set_entry(0, 0, "{GOTO 9zz}")
        app.names["M"] = {"sheet": "A", "range": ((0, 0), (0, 0))}
        app.handle(KeyEvent("char", ch="m", name="ALT+m"))
        assert app.error.startswith("Macro:")
        assert app.running

    def test_if_with_3d_name_cond(self, make_app):
        app, win = make_app()
        app.cmd_insert_sheet()
        app.sheets["B"].set_entry(0, 0, "5")
        app.names["TOT"] = {"sheet": "B", "range": ((0, 0), (0, 0))}
        app.sheet.set_entry(3, 0, "{IF TOT=5}{LET A1, 1}{ELSE}{LET A1, 2}{END}")
        MacroRunner(app).run_cell((3, 0))
        assert app.sheet.value_at(0, 0) == 1.0
