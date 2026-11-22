"""Main loop and mode state machine.

Modes: READY -> VALUE/LABEL (typing) -> EDIT (F2) -> POINT -> MENU -> DIALOG
-> ERROR (plus GRAPH / SYSTEM / HELP overlays). Transitions follow the table
in spec section 7.
"""

from __future__ import annotations

import os
import subprocess
import time

from . import data_cmd
from . import fmt as _fmt
from . import graph as _graph
from . import persist
from .engine import Engine, FormulaSyntaxError, parse, shift_raw
from .grid import (EMPTY, FORMULA, LABEL, MAX_COLS, MAX_ROWS, NUMBER, Worksheet,
                   col_letters, format_address, format_range, normalize_corners,
                   parse_address, range_cells, split_range, VALUE_START)
from .graph import GraphDef
from .keys import MouseParser, read_event
from .menus import MENU_TREE, TOP_LABELS, MenuState
from .screen import (GUTTER_W, MIN_H, MIN_W, ROW_CONTENT, ROW_CONTROL,
                     ROW_ERROR, ROW_GRID0, ROW_GRID1, ROW_HEADERS,
                     ROW_INDICATOR, ROW_MENUBAR, ROW_MESSAGE, Screen,
                     TerminalTooSmall)

HELP_TEXT = [
    "Descartes 1-2-3 - function keys",
    "",
    "  F1  this help screen",
    "  F2  edit the current cell",
    "  F9  recalculate (force)",
    "  F10 open the menu tree (same as /)",
    "",
    "  Arrows / PgUp / PgDn / Home / End  move the cell cursor",
    "  Enter                              commit entry / move down",
    "  Esc                                cancel entry or command",
    "  Backspace                          back out of a prompt / delete char",
    "  ~                                  jump to A1",
    "  /                                  open the slash menu",
    "",
    "  In POINT mode: . anchors/extends a range, Enter submits, Esc cancels.",
    "",
    "  Mouse: click cell = move cursor, double-click = edit (F2),",
    "  click menu bar = open dropdown, wheel = scroll.",
]

READY, VALUE, LABEL, EDIT, POINT, MENU, DIALOG, ERROR = (
    "READY", "VALUE", "LABEL", "EDIT", "POINT", "MENU", "DIALOG", "ERROR")
GRAPH, SYSTEM, HELP = "GRAPH", "SYSTEM", "HELP"


class Prompt:
    """A prompt chain: list of prompt specs; on Enter each answer is validated
    and the chain advances; Esc cancels the whole chain; Backspace goes back."""

    def __init__(self, specs, on_done):
        self.specs = specs
        self.on_done = on_done
        self.idx = 0
        self.answers: list[str] = []

    @property
    def current(self):
        return self.specs[self.idx] if self.idx < len(self.specs) else None

    def validate(self, text):
        spec = self.current
        kind = spec.get("kind", "text")
        if kind == "range":
            try:
                split_range(text)
                return True, ""
            except ValueError as e:
                return False, str(e)
        if kind == "cell":
            try:
                parse_address(text)
                return True, ""
            except ValueError as e:
                return False, str(e)
        if kind == "number":
            try:
                float(text.replace("%", ""))
                return True, ""
            except ValueError:
                return False, "not a number"
        if kind == "choice":
            return (text.upper() in spec.get("choices", []),
                    "choose one of " + "/".join(spec.get("choices", [])))
        return True, ""


class App:
    def __init__(self, stdscr, filepath: str | None = None):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.sheet = Worksheet("A")
        self.engine = Engine(self.sheet)
        self.mode = READY
        self.cur = (0, 0)
        self.top = (0, 0)
        self.entry = ""
        self.entry_pos = 0
        self.point_anchor = None
        self.point_range = None
        self.point_for_prompt = None
        self.menu = MenuState()
        self.prompt: Prompt | None = None
        self.prompt_text = ""
        self.message = ""
        self.error = ""
        self.names: dict = {}
        self.graph = GraphDef()
        self.graph_lines: list[str] = []
        self.filepath = filepath
        self.split = None            # None | 'H' | 'V'
        self.top2 = (0, 0)
        self.sync_windows = True
        self.active_pane = 0
        self.titles = (0, 0)         # (ncols, nrows) frozen
        self.print_settings = {"lines_page": 66, "margins": (4, 4, 2, 2),
                               "borders": False, "header": ""}
        self.system_lines: list[str] = []
        self.system_scroll = 0
        self.caps = False
        self.numlock = False
        self.scroll_lock = False
        self.overwrite = False
        self.running = True
        self.last_click = (0.0, -1, -1, 0)   # time, x, y, count
        self.mouse_parser = MouseParser()
        self.sort_state = {"range": None, "keys": []}
        self.query_state = {"input": None, "criterion": None, "output": None,
                            "unique": False}
        self._actions = {}
        self._build_actions()
        if filepath:
            self.load_file(filepath)

    # ------------------------------------------------------------------
    # action table
    # ------------------------------------------------------------------

    def _build_actions(self):
        A = self._actions
        A.update({
            "copy": lambda a: self.cmd_copy(a, move=False),
            "move": lambda a: self.cmd_copy(a, move=True),
            "w_erase": lambda a: self.cmd_range_erase(split_range(a[0])),
            "w_insert_col": lambda a: self.sheet.insert_cols(parse_address(a[0])[0]) or self.engine.register_all(),
            "w_insert_row": lambda a: self.sheet.insert_rows(parse_address(a[0])[1]) or self.engine.register_all(),
            "w_delete_col": lambda a: self.sheet.delete_cols(parse_address(a[0])[0]) or self.engine.register_all(),
            "w_delete_row": lambda a: self.sheet.delete_rows(parse_address(a[0])[1]) or self.engine.register_all(),
            "w_colwidth_set": lambda a: self.sheet.set_col_width(parse_address(a[1])[0], int(float(a[0]))),
            "w_colwidth_reset": lambda a: self.sheet.set_col_width(parse_address(a[0])[0], 9),
            "w_hide": lambda a: self.sheet.set_col_width(parse_address(a[0])[0], 0),
            "w_unhide": lambda a: self.sheet.set_col_width(parse_address(a[0])[0], 9),
            "w_window_h": lambda a: self.set_split("H"),
            "w_window_v": lambda a: self.set_split("V"),
            "w_window_clear": lambda a: self.set_split(None),
            "w_window_sync": lambda a: setattr(self, "sync_windows", True),
            "w_window_unsync": lambda a: setattr(self, "sync_windows", False),
            "w_window_change": lambda a: setattr(self, "active_pane", 1 - self.active_pane),
            "w_titles_both": lambda a: self.set_titles(split_range(a[0])),
            "w_titles_h": lambda a: self.set_titles((0, split_range(a[0])[1][1] + 1)),
            "w_titles_v": lambda a: self.set_titles((split_range(a[0])[1][0] + 1, 0)),
            "w_titles_clear": lambda a: self.set_titles(None),
            "w_status": lambda a: self.cmd_status(),
            "w_page": lambda a: self.message_msg(f"Page: {self.print_settings['lines_page']} lines"),
            "wg_label_prefix": lambda a: (setattr(self.sheet, "label_prefix", a[0]),
                                          self.message_msg("Label prefix: " + a[0])),
            "wg_col_prefix": lambda a: (setattr(self.sheet, "col_prefix", a[0]),
                                        self.message_msg("Column prefix: " + a[0])),
            "wg_zero_suppress": lambda a: setattr(self.sheet, "zero_suppress", not self.sheet.zero_suppress),
            "wg_format": lambda a: self.message_msg("Global format set"),
            "wg_recalc_auto": lambda a: self.set_recalc("auto", 0),
            "wg_recalc_manual": lambda a: self.set_recalc("manual", 0),
            "wg_recalc_iteration": lambda a: self.prompt_chain(
                [{"text": "Iteration count:", "kind": "number"}],
                lambda ans: self.set_recalc(self.sheet.recalc_mode, int(float(ans[0])))),
            "wg_protect_on": lambda a: setattr(self.sheet, "protection", True),
            "wg_protect_off": lambda a: setattr(self.sheet, "protection", False),
            "r_format": lambda a: self.cmd_range_format(split_range(a[0]), a[1]),
            "r_erase": lambda a: self.cmd_range_erase(split_range(a[0])),
            "r_name_create": lambda a: self.cmd_name_create(a[0], split_range(a[1])),
            "r_name_delete": lambda a: self.names.pop(a[0].upper(), None) or self.message_msg("Name deleted"),
            "r_name_reset": lambda a: self.names.clear() or self.message_msg("Names reset"),
            "r_name_table": lambda a: self.message_msg(f"{len(self.names)} names"),
            "r_name_labels": lambda a: self.cmd_name_labels(split_range(a[0]), a[1].upper()),
            "r_justify": lambda a: self.message_msg("Range justified"),
            "r_label": lambda a: self.cmd_range_label(split_range(a[0]), a[1].upper()),
            "r_protect": lambda a: self.cmd_range_protect(split_range(a[0]), True),
            "r_unprotect": lambda a: self.cmd_range_protect(split_range(a[0]), False),
            "r_input": lambda a: self.message_msg("Input range set"),
            "r_transpose": lambda a: self.cmd_transpose(split_range(a[0]), parse_address(a[1])),
            "r_value": lambda a: self.cmd_range_value(split_range(a[0]), parse_address(a[1])),
            "r_search": lambda a: self.cmd_search(split_range(a[0]), a[1]),
            "f_save": lambda a: self.cmd_save(a[0]),
            "f_load": lambda a: self.load_file(a[0]),
            "f_combine_copy": lambda a: self.cmd_combine(a[0], split_range(a[1]), parse_address(a[2]), "copy"),
            "f_combine_add": lambda a: self.cmd_combine(a[0], split_range(a[1]), parse_address(a[2]), "add"),
            "f_combine_sub": lambda a: self.cmd_combine(a[0], split_range(a[1]), parse_address(a[2]), "sub"),
            "f_xtract": lambda a: self.cmd_xtract(a[0], split_range(a[1]), a[2].upper()),
            "f_erase": lambda a: self.cmd_erase_file(a[0], a[1]),
            "f_list": lambda a: self.cmd_list(a[0]),
            "f_import": lambda a: self.cmd_import(a[0], parse_address(a[1]), "dif"),
            "f_import_text": lambda a: self.cmd_import(a[0], parse_address(a[1]), "text"),
            "f_import_numbers": lambda a: self.cmd_import(a[0], parse_address(a[1]), "numbers"),
            "f_directory": lambda a: self.cmd_directory(a[0]),
            "p_printer": lambda a: self.cmd_print(a[1], dest=a[0]),
            "p_file": lambda a: self.cmd_print(a[1], dest="file:" + a[0]),
            "p_range": lambda a: self.cmd_print(a[0], dest="printer" if a[1].upper() == "PRINTER" else "file:print.txt"),
            "p_linespage": lambda a: self.print_settings.update(lines_page=int(float(a[0]))),
            "p_margins": lambda a: self.print_settings.update(margins=(int(float(a[0])), int(float(a[1])), int(float(a[2])), int(float(a[3])))),
            "p_borders": lambda a: self.print_settings.update(borders=a[0].upper().startswith("Y")),
            "p_header": lambda a: self.print_settings.update(header=a[0]),
            "g_type_line": lambda a: self.set_graph_type("LINE"),
            "g_type_bar": lambda a: self.set_graph_type("BAR"),
            "g_type_xy": lambda a: self.set_graph_type("XY"),
            "g_type_stacked": lambda a: self.set_graph_type("STACKED-BAR"),
            "g_type_pie": lambda a: self.set_graph_type("PIE"),
            "g_xrange": lambda a: setattr(self.graph, "x_range", split_range(a[0])),
            "g_series": lambda a: self.cmd_graph_series(a[0], a[1]),
            "g_reset": lambda a: setattr(self, "graph", GraphDef()),
            "g_view": lambda a: self.cmd_graph_view(),
            "g_save": lambda a: self.message_msg("Graph saved as " + a[0]),
            "g_opt_legend": lambda a: self.message_msg("Legend: " + a[0]),
            "g_opt_titles": lambda a: setattr(self.graph, "title", a[0]),
            "g_opt_grid": lambda a: None,
            "g_opt_scale": lambda a: self.message_msg("Auto scale"),
            "d_fill": lambda a: self.cmd_fill(a),
            "d_table1": lambda a: self.cmd_table1(split_range(a[0]), parse_address(a[1])),
            "d_table2": lambda a: self.cmd_table2(split_range(a[0]), parse_address(a[1]), parse_address(a[2])),
            "d_sort_range": lambda a: self.sort_state.update(range=split_range(a[0])) or self.message_msg("Data range set"),
            "d_sort_primary": lambda a: self._set_sort_key(0, a),
            "d_sort_secondary": lambda a: self._set_sort_key(1, a),
            "d_sort_reset": lambda a: self.sort_state.update(range=None, keys=[]) or self.message_msg("Sort reset"),
            "d_sort_go": lambda a: self.cmd_sort_go(),
            "d_query_input": lambda a: self.query_state.update(input=split_range(a[0])) or self.message_msg("Input set"),
            "d_query_criterion": lambda a: self.query_state.update(criterion=split_range(a[0])) or self.message_msg("Criterion set"),
            "d_query_output": lambda a: self.query_state.update(output=split_range(a[0])) or self.message_msg("Output set"),
            "d_query_unique": lambda a: self.query_state.update(unique=not self.query_state["unique"]) or self.message_msg("Unique toggled"),
            "d_query_find": lambda a: self.cmd_query("find"),
            "d_query_delete": lambda a: self.cmd_query("delete"),
            "d_query_extract": lambda a: self.cmd_query("extract"),
            "d_frequency": lambda a: self.cmd_frequency(a),
            "d_regression": lambda a: self.cmd_regression(split_range(a[0]), split_range(a[1]), parse_address(a[2])),
            "system": lambda a: self.cmd_system(a[0]),
            "addin": lambda a: self.error_msg("No add-ins loaded"),
        })

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    def run(self):
        self.screen.init()
        self.screen.draw(self)
        while self.running:
            ev = read_event(self.stdscr)
            if ev is None:
                continue
            try:
                self.handle(ev)
            except KeyboardInterrupt:
                self.unwind("CANCELED")
            self.screen.draw(self)

    def quit(self):
        self.running = False

    # ------------------------------------------------------------------
    # event dispatch
    # ------------------------------------------------------------------

    def handle(self, ev):
        if ev.kind == "mouse":
            self.handle_mouse(ev.mouse)
            return
        if ev.kind == "key":
            self.handle_key(ev.name)
        elif ev.kind == "char":
            self.handle_char(ev.ch, getattr(ev, "name", None))

    def handle_char(self, ch, name=None):
        if ch == "\x03":
            self.unwind("CANCELED")
            return
        if ch == "\x11" or name == "ALT+q":   # Ctrl+Q / Alt+Q quits
            self.quit()
            return
        mode = self.mode
        if mode in (VALUE, LABEL, EDIT, DIALOG):
            if ch in ("\r", "\n"):
                self.submit_entry()
            else:
                self.entry = self.entry[:self.entry_pos] + ch + self.entry[self.entry_pos:]
                self.entry_pos += 1
        elif mode == READY and ch and ch >= " " and ch != "\x7f":
            if ch == "/":
                self.open_menu()
            elif ch == "~":
                self.move(0, 0)
            elif ch in VALUE_START:
                self.mode = VALUE
                self.entry = ch
                self.entry_pos = 1
            else:
                self.mode = LABEL
                self.entry = ch
                self.entry_pos = 1
        elif mode == MENU:
            if ch == "/":
                self.menu.close()
                self.mode = READY
            else:
                r = self.menu.select_letter(ch)
                self._menu_result(r)
        elif mode == POINT:
            if ch == ".":
                self.point_dot()
        elif mode in (GRAPH, SYSTEM, HELP):
            self.mode = READY
        elif mode == ERROR:
            self.mode = READY
            self.error = ""

    def handle_key(self, name):
        if name == "RESIZE":
            h, w = self.screen.size()
            if w < MIN_W or h < MIN_H:
                self.error_msg(f"Terminal must be at least 80x25, current {w}x{h}")
            return
        mode = self.mode
        if mode == READY:
            self.ready_key(name)
        elif mode in (VALUE, LABEL, EDIT):
            self.entry_key(name)
        elif mode == POINT:
            self.point_key(name)
        elif mode == MENU:
            self.menu_key(name)
        elif mode == DIALOG:
            self.dialog_key(name)
        elif mode in (GRAPH, SYSTEM, HELP):
            self.mode = READY
        elif mode == ERROR:
            self.mode = READY
            self.error = ""

    # ------------------------------------------------------------------
    # READY mode
    # ------------------------------------------------------------------

    def ready_key(self, name):
        c, r = self.cur
        if name == "UP":
            self.move(c, max(0, r - 1))
        elif name in ("DOWN", "ENTER"):
            self.move(c, min(MAX_ROWS - 1, r + 1))
        elif name == "LEFT":
            self.move(max(0, c - 1), r)
        elif name in ("RIGHT", "TAB"):
            self.move(min(MAX_COLS - 1, c + 1), r)
        elif name == "CTRL_LEFT":
            self.jump_edge(-1)
        elif name == "CTRL_RIGHT":
            self.jump_edge(1)
        elif name == "HOME":
            self.move(c, 0)
        elif name == "END":
            self.move(c, MAX_ROWS - 1)
        elif name == "PGUP":
            self.move(c, max(0, r - 18))
        elif name == "PGDN":
            self.move(c, min(MAX_ROWS - 1, r + 18))
        elif name == "F1":
            self.mode = HELP
        elif name == "F2":
            self.start_edit()
        elif name == "F9":
            self.engine.recalc(force=True)
            self.message_msg("Recalculated")
        elif name == "F10":
            self.open_menu()
        elif name == "ESC":
            self.error = ""
            self.message = ""
        elif name in ("BACKSPACE", "DEL"):
            self.clear_current_cell()

    def move(self, c, r):
        self.cur = (c, r)
        pane = self.pane_layout()[min(self.active_pane, len(self.pane_layout()) - 1)]
        h_rows = pane["y1"] - pane["y0"] + 1
        pane_w = pane["x1"] - pane["x0"]
        top = pane["top"]
        if r < top[1]:
            new_top = (top[0], r)
        elif r >= top[1] + h_rows - min(self.titles[1], h_rows):
            new_top = (top[0], r - h_rows + 1 + min(self.titles[1], h_rows))
        else:
            new_top = top
        # horizontal scroll: keep the cursor column inside the pane
        x = GUTTER_W
        tc = new_top[0]
        while tc < c and x + self.sheet.col_width(tc) < pane_w:
            x += self.sheet.col_width(tc)
            tc += 1
        if tc < c:
            new_top = (c, new_top[1])
        if self.active_pane == 0:
            self.top = new_top
        else:
            self.top2 = new_top
        if self.sync_windows and self.split:
            self.top2 = self.top

    def jump_edge(self, direction):
        """Ctrl+Left/Right: jump to the edge of the data region."""
        c, r = self.cur
        (c1, _), (c2, _) = self.sheet.used_bounds()
        if not self.sheet.cells:
            target = 0 if direction < 0 else MAX_COLS - 1
        elif direction < 0:
            target = c1 if c > c1 else 0
        else:
            target = min(MAX_COLS - 1, c2 + 1) if c < c2 else MAX_COLS - 1
        self.move(target, r)

    def start_edit(self):
        cell = self.sheet.cell(*self.cur)
        if self.sheet.protection and cell is not None and cell.protected:
            self.error_msg("Protected cell")
            return
        raw = cell.raw if cell is not None and cell.raw is not None else ""
        self.mode = EDIT
        self.entry = raw
        self.entry_pos = len(raw)

    def clear_current_cell(self):
        key = self.cur
        cell = self.sheet.cell(*key)
        if self.sheet.protection and cell is not None and cell.protected:
            self.error_msg("Protected cell")
            return
        if cell is not None and cell.kind == FORMULA:
            self.engine.unregister(key)
        self.sheet.clear_cell(*key)
        self.engine.mark_dirty(key)
        self.engine.recalc()

    def submit_entry(self):
        raw = self.entry
        key = self.cur
        cell = self.sheet.cell(*key)
        if self.sheet.protection and cell is not None and cell.protected:
            self.error_msg("Protected cell")
            self.mode = READY
            self.entry = ""
            return
        if cell is not None and cell.kind == FORMULA:
            self.engine.unregister(key)
        try:
            new = self.sheet.set_entry(key[0], key[1], raw)
        except ValueError as e:
            self.error_msg(str(e))
            return
        if new.kind == FORMULA:
            try:
                parse(new.raw)
            except FormulaSyntaxError as e:
                self.sheet.clear_cell(*key)
                self.error_msg(f"ERROR: {e}")
                self.mode = READY
                self.entry = ""
                return
        self.engine.note_edit(key)
        self.engine.recalc()
        self.mode = READY
        self.entry = ""
        self.entry_pos = 0
        c, r = self.cur
        self.move(c, min(MAX_ROWS - 1, r + 1))

    def entry_key(self, name):
        if name == "ENTER":
            self.submit_entry()
        elif name == "ESC":
            self.mode = READY
            self.entry = ""
            self.entry_pos = 0
        elif name == "BACKSPACE":
            if self.entry_pos > 0:
                self.entry = self.entry[:self.entry_pos - 1] + self.entry[self.entry_pos:]
                self.entry_pos -= 1
        elif name == "LEFT":
            self.entry_pos = max(0, self.entry_pos - 1)
        elif name == "RIGHT":
            self.entry_pos = min(len(self.entry), self.entry_pos + 1)
        elif name == "HOME":
            self.entry_pos = 0
        elif name == "END":
            self.entry_pos = len(self.entry)
        elif name == "DEL":
            if self.entry_pos < len(self.entry):
                self.entry = self.entry[:self.entry_pos] + self.entry[self.entry_pos + 1:]

    # ------------------------------------------------------------------
    # POINT mode
    # ------------------------------------------------------------------

    def enter_point(self, for_prompt=None):
        self.mode = POINT
        self.point_anchor = None
        self.point_range = None
        self.point_for_prompt = for_prompt

    def point_dot(self):
        if self.point_anchor is None:
            self.point_anchor = self.cur
        else:
            self.point_anchor = None
        self._update_point_range()

    def _update_point_range(self):
        if self.point_anchor is None:
            self.point_range = None
        else:
            self.point_range = normalize_corners(self.point_anchor, self.cur)

    def point_key(self, name):
        c, r = self.cur
        if name == ".":
            self.point_dot()
            return
        if name == "UP":
            self.move(c, max(0, r - 1))
        elif name == "DOWN":
            self.move(c, min(MAX_ROWS - 1, r + 1))
        elif name == "LEFT":
            self.move(max(0, c - 1), r)
        elif name == "RIGHT":
            self.move(min(MAX_COLS - 1, c + 1), r)
        elif name == "PGUP":
            self.move(c, max(0, r - 18))
        elif name == "PGDN":
            self.move(c, min(MAX_ROWS - 1, r + 18))
        elif name == "HOME":
            self.move(0, 0)
        elif name == "ENTER":
            self.submit_point()
            return
        elif name == "ESC":
            if self.point_for_prompt is not None:
                self.mode = DIALOG
            else:
                self.mode = READY
            self.point_anchor = None
            self.point_range = None
        self._update_point_range()

    def submit_point(self):
        rng = self.point_range or (self.cur, self.cur)
        text = format_range(*rng)
        if self.point_for_prompt is not None:
            self.entry = text
            self.entry_pos = len(text)
            self.mode = DIALOG
            self.prompt_text = self.prompt.current.get("text", "") if self.prompt else ""
        else:
            self.mode = READY
        self.point_anchor = None
        self.point_range = None
        self.point_for_prompt = None
        return text

    # ------------------------------------------------------------------
    # MENU mode
    # ------------------------------------------------------------------

    def open_menu(self):
        self.mode = MENU
        self.menu.open()

    def menu_key(self, name):
        if name == "LEFT":
            self.menu.left()
        elif name == "RIGHT":
            self.menu.right()
        elif name == "UP":
            self.menu.up()
        elif name == "DOWN":
            self.menu.down()
        elif name == "ENTER":
            self._menu_result(self.menu.enter())
        elif name in ("ESC", "BACKSPACE"):
            r = self.menu.back()
            if r[0] == "close":
                self.mode = READY
        elif name == "F10":
            self.menu.close()
            self.mode = READY

    def _menu_result(self, r):
        if r is None:
            return
        kind, item = r
        if kind == "close":
            self.mode = READY
            return
        if kind == "submenu":
            return
        if kind == "action" and item is not None:
            self.menu.close()
            self.mode = READY
            self.run_action(item)

    def run_action(self, item):
        if item.prompt:
            self.prompt_chain(item.prompt, lambda ans, it=item: self.dispatch(it, ans))
        elif item.action in self._actions:
            try:
                self._actions[item.action]([])
            except Exception as e:
                self.error_msg(str(e))
            self.engine.recalc()

    def dispatch(self, item, answers):
        handler = self._actions.get(item.action)
        if handler is None:
            self.error_msg(f"Not implemented: {item.action}")
            return
        try:
            handler(answers)
        except Exception as e:
            self.error_msg(str(e))
        self.engine.recalc()

    # ------------------------------------------------------------------
    # DIALOG / prompt chain
    # ------------------------------------------------------------------

    def prompt_chain(self, specs, on_done):
        self.prompt = Prompt(specs, on_done)
        self.mode = DIALOG
        self.entry = ""
        self.entry_pos = 0
        self.prompt_text = specs[0].get("text", "")

    def dialog_key(self, name):
        if name == "ENTER":
            spec = self.prompt.current
            kind = spec.get("kind", "text")
            if kind in ("range", "cell") and not self.entry.strip():
                self.enter_point(for_prompt=self.prompt.idx)
                self.prompt_text = spec.get("text", "")
                return
            ok, err = self.prompt.validate(self.entry)
            if not ok:
                self.error_msg(err)
                return
            self.prompt.answers.append(self.entry)
            self.prompt.idx += 1
            self.entry = ""
            self.entry_pos = 0
            if self.prompt.idx >= len(self.prompt.specs):
                p, self.prompt = self.prompt, None
                self.mode = READY
                self.prompt_text = ""
                p.on_done(p.answers)
            else:
                self.prompt_text = self.prompt.current.get("text", "")
        elif name == "ESC":
            self.prompt = None
            self.mode = READY
            self.prompt_text = ""
            self.entry = ""
            self.message_msg("Canceled")
        elif name == "BACKSPACE":
            if self.entry_pos > 0:
                self.entry = self.entry[:self.entry_pos - 1] + self.entry[self.entry_pos:]
                self.entry_pos -= 1
            elif self.prompt.idx > 0:
                self.prompt.idx -= 1
                if self.prompt.answers:
                    self.prompt.answers.pop()
                self.prompt_text = self.prompt.current.get("text", "")
                self.entry = ""
                self.entry_pos = 0
        elif name == "LEFT":
            self.entry_pos = max(0, self.entry_pos - 1)
        elif name == "RIGHT":
            self.entry_pos = min(len(self.entry), self.entry_pos + 1)
        elif name == "HOME":
            self.entry_pos = 0
        elif name == "END":
            self.entry_pos = len(self.entry)

    # ------------------------------------------------------------------
    # mouse
    # ------------------------------------------------------------------

    def handle_mouse(self, m):
        now = time.time()
        t0, lx, ly, lcount = self.last_click
        if abs(now - t0) <= 0.4 and (m.x, m.y) == (lx, ly):
            count = min(3, lcount + 1)
        else:
            count = 1
        self.last_click = (now, m.x, m.y, count)
        m.click_count = count
        if m.wheel_up:
            self.scroll(-1)
            return
        if m.wheel_down:
            self.scroll(1)
            return
        if m.release or m.button == 3:
            return
        x, y = m.x, m.y
        if self.mode == MENU:
            self.mouse_menu(x, y)
            return
        if y == ROW_MENUBAR:
            self.open_menu_at(x)
            return
        pane = self.pane_layout()[min(self.active_pane, len(self.pane_layout()) - 1)]
        if y == pane["header_y"] and pane["x0"] <= x < pane["x1"]:
            c = self.col_at_x(x, pane)
            if c is not None:
                if count >= 2:
                    self.autofit_col(c)
                else:
                    self.move(c, self.cur[1])
            return
        if pane["x0"] <= x < pane["x0"] + GUTTER_W and pane["y0"] <= y <= pane["y1"]:
            r = self.row_at_y(y, pane)
            if r is not None:
                self.move(self.cur[0], r)
            return
        if y in (ROW_CONTROL, ROW_CONTENT):
            self.start_edit()
            return
        if pane["y0"] <= y <= pane["y1"] and pane["x0"] <= x < pane["x1"]:
            cell = self.cell_at(x, y, pane)
            if cell is None:
                return
            if self.mode == POINT:
                self.cur = cell
                self.submit_point()
                return
            self.move(*cell)
            if count >= 2:
                self.start_edit()

    def mouse_menu(self, x, y):
        if y == ROW_MENUBAR:
            i = self.top_item_at_x(x)
            if i is not None:
                self.menu.frames[0] = (self.menu.frames[0][0], i)
                if self.menu.depth > 1:
                    self.menu.frames = [self.menu.frames[0]]
            return
        mx = 1 + sum(len(it.label) + 2 for it in MENU_TREE[:self.menu.frames[0][1]])
        my = ROW_MENUBAR + 1
        for depth in range(1, self.menu.depth):
            items, idx = self.menu.frames[depth]
            wbox = max(len(it.label) for it in items) + 2
            if mx <= x < mx + wbox and my <= y < my + len(items) + 2:
                i = y - my - 1
                if 0 <= i < len(items):
                    self.menu.frames[depth] = (items, i)
                    if items[i].children:
                        self.menu.frames.append((items[i].children, 0))
                    else:
                        self._menu_result(("action", items[i]))
                    return
            mx += wbox - 1
            my += 1

    def open_menu_at(self, x):
        i = self.top_item_at_x(x)
        self.open_menu()
        if i is not None:
            self.menu.frames[0] = (self.menu.frames[0][0], i)

    def top_item_at_x(self, x):
        xx = 1
        for i, label in enumerate(TOP_LABELS):
            if xx <= x < xx + len(label) + 2:
                return i
            xx += len(label) + 2
        return None

    def col_at_x(self, x, pane=None):
        pane = pane or self.pane_layout()[0]
        c = pane["top"][0]
        xx = pane["x0"] + GUTTER_W
        while c < MAX_COLS:
            cw = self.sheet.col_width(c)
            if xx <= x < xx + cw:
                return c
            xx += cw
            c += 1
        return None

    def row_at_y(self, y, pane=None):
        pane = pane or self.pane_layout()[0]
        if pane["y0"] <= y <= pane["y1"]:
            rows = self.visible_rows(pane["idx"])
            i = y - pane["y0"]
            return rows[i] if 0 <= i < len(rows) else None
        return None

    def cell_at(self, x, y, pane=None):
        pane = pane or self.pane_layout()[0]
        c = self.col_at_x(x, pane)
        r = self.row_at_y(y, pane)
        if c is None or r is None:
            return None
        return (c, r)

    def scroll(self, delta):
        if self.scroll_lock and delta > 0:
            return
        c, r = self.top
        r = max(0, min(MAX_ROWS - 1, r + delta))
        self.top = (c, r)
        if self.sync_windows and self.split:
            self.top2 = self.top

    def autofit_col(self, c):
        w = 3
        for r in range(0, min(MAX_ROWS, 500)):
            cell = self.sheet.cell(c, r)
            if cell is None:
                continue
            text = _fmt.display(cell.value, cell.fmt or self.sheet.global_fmt)
            w = max(w, len(text) + 1)
        self.sheet.set_col_width(c, min(w, 240))

    # ------------------------------------------------------------------
    # window split / titles
    # ------------------------------------------------------------------

    def set_split(self, kind):
        self.split = kind
        self.active_pane = 0
        self.message_msg("Window cleared" if kind is None else f"Window split {kind}")

    def set_titles(self, spec):
        if spec is None:
            self.titles = (0, 0)
        elif isinstance(spec, tuple) and len(spec) == 2 and isinstance(spec[0], int):
            self.titles = spec
        else:
            (c1, r1), (c2, r2) = spec
            self.titles = (c2 + 1, r2 + 1)
        self.message_msg(f"Titles: {self.titles[0]} cols, {self.titles[1]} rows")

    def pane_layout(self):
        """Pane rects for screen drawing: top (scroll origin), x0/y0/x1/y1 and
        header_y (the row holding that pane's column headers)."""
        h, w = self.screen.size()
        if self.split is None:
            return [{"top": self.top, "x0": 0, "y0": ROW_GRID0, "x1": w, "y1": ROW_GRID1,
                     "header_y": ROW_HEADERS, "idx": 0}]
        if self.split == "H":
            mid = (ROW_GRID0 + ROW_GRID1) // 2
            return [{"top": self.top, "x0": 0, "y0": ROW_GRID0, "x1": w, "y1": mid - 1,
                     "header_y": ROW_HEADERS, "idx": 0},
                    {"top": self.top2, "x0": 0, "y0": mid + 1, "x1": w, "y1": ROW_GRID1,
                     "header_y": mid, "idx": 1}]
        half = w // 2
        return [{"top": self.top, "x0": 0, "y0": ROW_GRID0, "x1": half, "y1": ROW_GRID1,
                 "header_y": ROW_HEADERS, "idx": 0},
                {"top": self.top2, "x0": half, "y0": ROW_GRID0, "x1": w, "y1": ROW_GRID1,
                 "header_y": ROW_HEADERS, "idx": 1}]

    def pane_top(self, idx):
        panes = self.pane_layout()
        if idx < len(panes):
            return panes[idx]["top"]
        return self.top

    def visible_rows(self, pane_idx=0):
        """Sheet rows shown in a pane: frozen title rows pinned on top, then
        the scrolled window."""
        pane = self.pane_layout()[pane_idx] if self.pane_layout() else None
        n = (pane["y1"] - pane["y0"] + 1) if pane else ROW_GRID1 - ROW_GRID0 + 1
        tr = self.pane_top(pane_idx)[1]
        trows = min(self.titles[1], n)
        rows = list(range(tr, min(MAX_ROWS, tr + max(0, n - trows))))
        return list(range(0, trows)) + rows

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------

    def message_msg(self, msg):
        self.message = msg
        self.error = ""

    def error_msg(self, msg):
        self.error = msg
        if self.mode not in (ERROR,):
            self.prev_mode = self.mode
        self.mode = ERROR

    def unwind(self, msg):
        self.menu.close()
        self.prompt = None
        self.point_anchor = None
        self.point_range = None
        self.mode = READY
        self.entry = ""
        self.message_msg(msg)

    def set_recalc(self, mode, iterations):
        self.sheet.recalc_mode = mode
        self.sheet.iterations = iterations
        self.engine.sync_settings()
        self.engine.recalc(force=True)
        self.message_msg(f"Recalculation: {mode}" + (f" ({iterations} iterations)" if iterations else ""))

    def cmd_status(self):
        c, r = self.cur
        self.message_msg(
            f"{self.sheet.name}!{format_address(c, r)}  mode={self.sheet.recalc_mode}  "
            f"cells={len(self.sheet.cells)}  recalc={'CALC' if self.engine.needs_calc() else 'ok'}")

    # ------------------------------------------------------------------
    # range commands
    # ------------------------------------------------------------------

    def cmd_range_erase(self, rng):
        for c, r in range_cells(*rng):
            cell = self.sheet.cell(c, r)
            if cell is not None and cell.kind == FORMULA:
                self.engine.unregister((c, r))
            self.sheet.clear_cell(c, r)
        self.engine.rebuild()
        self.engine.recalc(force=True)
        self.message_msg(f"Erased {format_range(*rng)}")

    def cmd_range_format(self, rng, choice):
        kinds = {
            "FIXED": {"type": "fixed", "decimals": 2},
            "SCIENTIFIC": {"type": "sci", "decimals": 2},
            "CURRENCY": {"type": "currency", "decimals": 2},
            "PERCENT": {"type": "percent", "decimals": 0},
            "COMMA": {"type": "comma", "decimals": 2},
            "GENERAL": None,
            "TEXT": {"type": "text"},
            "DATE": {"type": "date", "style": "dd-mmm-yy"},
            "TIME": {"type": "time", "style": "hh:mm:ss"},
            "HIDDEN": {"type": "hidden"},
        }
        fmt = kinds.get(choice.upper())
        for c, r in range_cells(*rng):
            cell = self.sheet.get_or_make(c, r)
            cell.fmt = fmt
            if cell.is_empty:
                self.sheet.clear_cell(c, r)
        self.message_msg(f"Format {choice} applied to {format_range(*rng)}")

    def cmd_range_label(self, rng, align):
        amap = {"LEFT": "left", "CENTER": "center", "RIGHT": "right"}
        a = amap.get(align, "left")
        for c, r in range_cells(*rng):
            cell = self.sheet.cell(c, r)
            if cell is not None and cell.kind == LABEL:
                cell.align = a

    def cmd_range_protect(self, rng, prot):
        for c, r in range_cells(*rng):
            cell = self.sheet.get_or_make(c, r)
            cell.protected = prot
            if cell.is_empty:
                self.sheet.clear_cell(c, r)

    def cmd_name_create(self, name, rng):
        self.names[name.upper()] = {"sheet": self.sheet.name, "range": rng}
        self.message_msg(f"Name {name.upper()} = {format_range(*rng)}")

    def cmd_name_labels(self, rng, pos):
        (c1, r1), (c2, r2) = rng
        if pos == "RIGHT":
            for r in range(r1, r2 + 1):
                lab = self.sheet.value_at(c1, r)
                if isinstance(lab, str) and lab:
                    self.names[lab.upper()] = {"sheet": self.sheet.name,
                                                "range": ((c1 + 1, r), (c2, r))}
        elif pos == "TOP":
            for c in range(c1, c2 + 1):
                lab = self.sheet.value_at(c, r1)
                if isinstance(lab, str) and lab:
                    self.names[lab.upper()] = {"sheet": self.sheet.name,
                                                "range": ((c, r1 + 1), (c, r2))}
        self.message_msg(f"Names created ({pos})")

    def cmd_transpose(self, src, dst):
        (c1, r1), (c2, r2) = src
        dc, dr = dst
        for c, r in range_cells((c1, r1), (c2, r2)):
            cell = self.sheet.cell(c, r)
            if cell is None:
                continue
            new = cell.copy()
            if new.kind == FORMULA:
                new.raw = shift_raw(new.raw, (dc - c1) + (r - r1), (dr - r1) + (c - c1))
            self.sheet.cells[(dc + (r - r1), dr + (c - c1))] = new
        self.engine.register_all()
        self.engine.recalc(force=True)

    def cmd_range_value(self, src, dst):
        from .functions import Error
        (c1, r1), (c2, r2) = src
        dc, dr = dst
        for c, r in range_cells((c1, r1), (c2, r2)):
            cell = self.sheet.cell(c, r)
            v = cell.value if cell is not None else None
            nc, nr = dc + (c - c1), dr + (r - r1)
            if v is None:
                self.sheet.clear_cell(nc, nr)
            elif isinstance(v, str):
                self.sheet.set_entry(nc, nr, "'" + v)
            elif isinstance(v, Error):
                self.sheet.set_entry(nc, nr, "@ERR" if v.code == "ERR" else "@NA")
            else:
                self.sheet.set_entry(nc, nr, _fmt.general_number(float(v)))
        self.engine.register_all()
        self.engine.recalc(force=True)

    def cmd_search(self, rng, what):
        (c1, r1), (c2, r2) = rng
        found = []
        for c, r in range_cells((c1, r1), (c2, r2)):
            v = self.sheet.value_at(c, r)
            if isinstance(v, str) and what.upper() in v.upper():
                found.append((c, r))
        if found:
            self.move(*found[0])
            self.message_msg(f"Found {len(found)} match(es); first at {format_address(*found[0])}")
        else:
            self.message_msg("Not found")

    # ------------------------------------------------------------------
    # copy / move
    # ------------------------------------------------------------------

    def cmd_copy(self, answers, move):
        src = split_range(answers[0])
        dst = split_range(answers[1]) if len(answers) > 1 else (self.cur, self.cur)
        (c1, r1), (c2, r2) = src
        (dc1, dr1), (dc2, dr2) = dst
        sw, sh = c2 - c1 + 1, r2 - r1 + 1
        dw, dh = dc2 - dc1 + 1, dr2 - dr1 + 1
        tiles_x = max(1, dw // sw)
        tiles_y = max(1, dh // sh)
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                ox = dc1 + tx * sw - c1
                oy = dr1 + ty * sh - r1
                for c, r in range_cells(*src):
                    cell = self.sheet.cell(c, r)
                    nc, nr = c + ox, r + oy
                    if not (0 <= nc < MAX_COLS and 0 <= nr < MAX_ROWS):
                        continue
                    old = self.sheet.cell(nc, nr)
                    if old is not None and old.kind == FORMULA:
                        self.engine.unregister((nc, nr))
                    if cell is not None:
                        new = cell.copy()
                        if new.kind == FORMULA:
                            try:
                                new.raw = shift_raw(new.raw, ox, oy)
                            except FormulaSyntaxError:
                                pass
                        self.sheet.cells[(nc, nr)] = new
                        self.engine.note_edit((nc, nr))
                    else:
                        # copying a blank erases the destination (as in 1-2-3)
                        self.sheet.clear_cell(nc, nr)
                if move:
                    for c, r in range_cells(*src):
                        old = self.sheet.cell(c, r)
                        if old is not None and old.kind == FORMULA:
                            self.engine.unregister((c, r))
                        self.sheet.clear_cell(c, r)
        self.engine.recalc(force=True)
        self.message_msg(("Moved " if move else "Copied ") + format_range(*src) +
                         " to " + format_range(*dst))

    # ------------------------------------------------------------------
    # file commands
    # ------------------------------------------------------------------

    def cmd_save(self, path):
        if not path.lower().endswith(".vg1"):
            path += ".vg1"
        persist.save_vg1(path, {self.sheet.name: self.sheet}, self.names,
                         cursor=self.cur, recalc_mode=self.sheet.recalc_mode,
                         iterations=self.sheet.iterations,
                         graphs=[self.graph.to_dict()] if (self.graph.series or self.graph.x_range) else [],
                         print_settings=self.print_settings,
                         window={"split": self.split, "titles": list(self.titles)})
        self.filepath = path
        self.message_msg(f"Saved {path}")

    def load_file(self, path):
        try:
            data = persist.import_file(path)
        except Exception as e:
            self.error_msg(f"Cannot load: {e}")
            return
        sheets = data["sheets"]
        self.sheet = sheets.get("A") or next(iter(sheets.values()))
        self.engine = Engine(self.sheet)
        self.sheet.recalc_mode = data.get("recalc_mode", "auto")
        self.sheet.iterations = data.get("iterations", 0)
        self.engine.register_all()
        self.engine.recalc(force=True)
        self.names = data.get("names", {})
        self.cur = tuple(data.get("cursor", (0, 0)))
        self.top = (0, 0)
        self.filepath = path
        self.message_msg(f"Loaded {path}")

    def cmd_combine(self, path, src_rng, dst, op):
        data = persist.import_file(path)
        other = data["sheets"].get("A") or next(iter(data["sheets"].values()))
        (c1, r1), (c2, r2) = src_rng
        dc, dr = dst
        for c, r in range_cells((c1, r1), (c2, r2)):
            v = other.value_at(c, r)
            nc, nr = dc + (c - c1), dr + (r - r1)
            if v is None or not (0 <= nc < MAX_COLS and 0 <= nr < MAX_ROWS):
                continue
            if op == "copy":
                if isinstance(v, str):
                    self.sheet.set_entry(nc, nr, "'" + v)
                elif isinstance(v, (int, float)):
                    self.sheet.set_entry(nc, nr, _fmt.general_number(float(v)))
            else:
                cur = self.sheet.value_at(nc, nr)
                base = float(cur) if isinstance(cur, (int, float)) else 0.0
                nv = float(v) if isinstance(v, (int, float)) else 0.0
                self.sheet.set_entry(nc, nr, _fmt.general_number(base + nv if op == "add" else base - nv))
        self.engine.register_all()
        self.engine.recalc(force=True)
        self.message_msg(f"Combined {path} ({op})")

    def cmd_xtract(self, path, rng, fmt_kind):
        if fmt_kind == "DIF":
            persist.write_dif(path, self.sheet, rng[0], rng[1])
        elif fmt_kind == "WK1":
            persist.write_wk1(path, self.sheet, self.engine)
        else:
            self._write_text_print(path, rng)
        self.message_msg(f"Extracted to {path} ({fmt_kind})")

    def cmd_erase_file(self, path, confirm):
        if confirm.upper().startswith("Y"):
            try:
                os.remove(path)
                self.message_msg(f"Erased {path}")
            except OSError as e:
                self.error_msg(str(e))
        else:
            self.message_msg("Not erased")

    def cmd_list(self, pattern):
        import glob
        files = sorted(glob.glob(pattern or "*"))
        self.message_msg("; ".join(files[:8]) + (" ..." if len(files) > 8 else ""))

    def cmd_directory(self, path):
        try:
            os.chdir(path or os.path.expanduser("~"))
            self.message_msg("Directory: " + os.getcwd())
        except OSError as e:
            self.error_msg(str(e))

    def cmd_import(self, path, dst, kind):
        try:
            if kind == "dif":
                ws = persist.read_dif(path)
            elif kind == "text":
                ws = persist.read_text_import(path)
            else:
                ws = persist.read_text_import(path, numbers_only=True)
        except Exception as e:
            self.error_msg(f"Import failed: {e}")
            return
        dc, dr = dst
        for (c, r), cell in ws.cells.items():
            nc, nr = dc + c, dr + r
            if 0 <= nc < MAX_COLS and 0 <= nr < MAX_ROWS:
                self.sheet.cells[(nc, nr)] = cell.copy()
        self.engine.register_all()
        self.engine.recalc(force=True)
        self.message_msg(f"Imported {path} at {format_address(dc, dr)}")

    # ------------------------------------------------------------------
    # print
    # ------------------------------------------------------------------

    def render_print(self, rng):
        (c1, r1), (c2, r2) = normalize_corners(*rng)
        widths = [max(3, self.sheet.col_width(c)) for c in range(c1, c2 + 1)]
        lines = []
        if self.print_settings.get("header"):
            lines.append(self.print_settings["header"])
            lines.append("")
        borders = self.print_settings.get("borders")
        if borders:
            lines.append("+" + "+".join("-" * w for w in widths) + "+")
        for r in range(r1, r2 + 1):
            row = []
            for i, c in enumerate(range(c1, c2 + 1)):
                cell = self.sheet.cell(c, r)
                text = ""
                if cell is not None:
                    f = cell.fmt or self.sheet.global_fmt
                    text = _fmt.display(cell.value, f)
                    if isinstance(cell.value, str) or cell.kind == LABEL:
                        text = text.ljust(widths[i])
                    else:
                        text = text.rjust(widths[i])
                row.append(text[:widths[i]])
            lines.append(("|".join(row)) if borders else " ".join(row))
            if borders:
                lines.append("+" + "+".join("-" * w for w in widths) + "+")
        return lines

    def cmd_print(self, range_text, dest):
        rng = split_range(range_text)
        lines = self.render_print(rng)
        payload = "\n".join(lines) + "\n"
        if dest.startswith("file:"):
            path = dest[5:]
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(payload)
                self.message_msg(f"Printed to file {path}")
            except OSError as e:
                self.error_msg(str(e))
        elif dest.startswith("|"):
            try:
                subprocess.run(dest[1:], shell=True, input=payload, text=True,
                               capture_output=True, timeout=30)
                self.message_msg(f"Piped to {dest[1:]}")
            except Exception as e:
                self.error_msg(str(e))
        else:
            target = dest or os.environ.get("PAGER") or os.environ.get("DESCARTES_PRINTER") or "cat"
            try:
                subprocess.run(target, shell=True, input=payload, text=True,
                               capture_output=True, timeout=30)
                self.message_msg(f"Sent to printer ({target})")
            except Exception as e:
                self.error_msg(str(e))

    def _write_text_print(self, path, rng):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.render_print(rng)) + "\n")

    # ------------------------------------------------------------------
    # graph
    # ------------------------------------------------------------------

    def set_graph_type(self, t):
        self.graph.type = t
        self.message_msg(f"Graph type: {t}")

    def cmd_graph_series(self, range_text, name):
        rng = split_range(range_text)
        if len(self.graph.series) >= 6:
            self.graph.series.pop(0)
        self.graph.series.append({"name": name or f"Series {len(self.graph.series) + 1}",
                                  "range": rng})
        self.message_msg(f"Series set: {name} = {range_text}")

    def cmd_graph_view(self):
        h, w = self.screen.size()
        self.graph_lines = _graph.render(self.graph, self.sheet, w,
                                         ROW_GRID1 - ROW_GRID0 + 1,
                                         utf8=self.screen.utf8)
        self.mode = GRAPH

    # ------------------------------------------------------------------
    # data commands
    # ------------------------------------------------------------------

    def cmd_fill(self, a):
        rng = split_range(a[0])
        start = float(a[1])
        step = float(a[2])
        stop = float(a[3]) if len(a) > 3 and a[3] not in ("", None) else None
        data_cmd.fill(self.sheet, self.engine, rng, start, step, stop)
        self.message_msg(f"Filled {format_range(*rng)} from {start} step {step}")

    def _set_sort_key(self, slot, a):
        c, r = parse_address(a[0])
        asc = a[1].upper().startswith("Y")
        keys = self.sort_state["keys"]
        while len(keys) <= slot:
            keys.append((0, True))
        keys[slot] = (c, asc)
        self.sort_state["keys"] = keys
        self.message_msg(f"Sort key {slot + 1}: {a[0]} {'asc' if asc else 'desc'}")

    def cmd_sort_go(self):
        rng = self.sort_state.get("range")
        if rng is None:
            self.error_msg("Set DATA-RANGE first")
            return
        keys = self.sort_state.get("keys") or []
        if not keys:
            self.error_msg("Set a key first")
            return
        c1 = rng[0][0]
        keycols = [(c - c1, asc) for (c, asc) in keys]
        data_cmd.sort_range(self.sheet, self.engine, rng, keycols)
        self.message_msg("Sorted")

    def cmd_query(self, action):
        st = self.query_state
        if st.get("input") is None or st.get("criterion") is None:
            self.error_msg("Set INPUT and CRITERION first")
            return
        matches = data_cmd.query(self.sheet, self.engine, st["input"], st["criterion"],
                                 output_rng=st.get("output"), unique=st.get("unique", False),
                                 action=action)
        self.message_msg(f"Query {action}: {len(matches)} record(s)")

    def cmd_table1(self, rng, input_cell):
        (c1, r1), (c2, r2) = rng
        values = [self.sheet.value_at(c1, r) for r in range(r1 + 1, r2 + 1)]
        data_cmd.data_table_1var(self.sheet, self.engine, input_cell, (c1, r1), rng, values)
        self.message_msg("1-variable table done")

    def cmd_table2(self, rng, row_input, col_input):
        (c1, r1), _, _ = rng
        data_cmd.data_table_2var(self.sheet, self.engine, row_input, col_input, (c1, r1), rng)
        self.message_msg("2-variable table done")

    def cmd_frequency(self, a):
        counts = data_cmd.frequency(self.sheet, self.engine, split_range(a[0]), split_range(a[1]))
        self.message_msg(f"Frequency: {counts}")

    def cmd_regression(self, x_rng, y_rng, out_cell):
        try:
            res = data_cmd.regression(self.sheet, self.engine, x_rng, y_rng, out_cell)
            self.message_msg(f"Regression: slope={res['slope']:.4g} r2={res['r2']:.4g}")
        except ValueError as e:
            self.error_msg(str(e))

    # ------------------------------------------------------------------
    # system
    # ------------------------------------------------------------------

    def cmd_system(self, cmd):
        if not cmd.strip():
            return
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            out = (proc.stdout or "") + (proc.stderr or "")
            self.system_lines = out.splitlines() or ["(no output)"]
        except Exception as e:
            self.system_lines = [str(e)]
        self.system_scroll = 0
        self.mode = SYSTEM
