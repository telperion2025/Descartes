"""Slash-menu tree data + menu UI state machine (pure logic; no curses).

Top-level order (1-2-3 3.1): WORKSHEET RANGE COPY MOVE FILE PRINT GRAPH DATA
SYSTEM (ADD-IN as a stub). Each item either opens a submenu or runs an action
identified by a string id that app.py dispatches.
"""

from __future__ import annotations


class MenuItem:
    __slots__ = ("label", "children", "action", "prompt")

    def __init__(self, label, children=None, action=None, prompt=None):
        self.label = label
        self.children = children      # list[MenuItem] or None
        self.action = action          # action id string or None
        self.prompt = prompt          # prompt-chain spec or None

    @property
    def is_leaf(self):
        return self.children is None


def _m(label, children=None, action=None, prompt=None):
    return MenuItem(label, children, action, prompt)


# Prompt-chain specs: list of prompt dicts {"text":..., "kind": "range"|"cell"|"text"|"file"|...}
RANGE_PROMPT = {"text": "", "kind": "range"}
CELL_PROMPT = {"text": "", "kind": "cell"}

MENU_TREE = [
    _m("WORKSHEET", [
        _m("GLOBAL", [
            _m("LABEL-PREFIX", action="wg_label_prefix"),
            _m("COL-PREFIX", action="wg_col_prefix"),
            _m("ZERO-SUPPRESS", action="wg_zero_suppress"),
            _m("FORMAT", action="wg_format"),
            _m("RECALCULATION", [
                _m("AUTO", action="wg_recalc_auto"),
                _m("MANUAL", action="wg_recalc_manual"),
                _m("ITERATION", action="wg_recalc_iteration"),
            ]),
            _m("PROTECTION", [
                _m("ENABLE", action="wg_protect_on"),
                _m("DISABLE", action="wg_protect_off"),
            ]),
        ]),
        _m("INSERT", [
            _m("COLUMN", action="w_insert_col", prompt=[{"text": "Insert columns at:", "kind": "cell"}]),
            _m("ROW", action="w_insert_row", prompt=[{"text": "Insert rows at:", "kind": "cell"}]),
        ]),
        _m("DELETE", [
            _m("COLUMN", action="w_delete_col", prompt=[{"text": "Delete columns at:", "kind": "cell"}]),
            _m("ROW", action="w_delete_row", prompt=[{"text": "Delete rows at:", "kind": "cell"}]),
        ]),
        _m("ERASE", action="w_erase", prompt=[{"text": "Erase range:", "kind": "range"}]),
        _m("COLUMN-WIDTH", [
            _m("SET", action="w_colwidth_set", prompt=[{"text": "Column width:", "kind": "number"},
                                                       {"text": "Column:", "kind": "cell"}]),
            _m("RESET", action="w_colwidth_reset", prompt=[{"text": "Column:", "kind": "cell"}]),
        ]),
        _m("HIDE", action="w_hide", prompt=[{"text": "Hide column:", "kind": "cell"}]),
        _m("UNHIDE", action="w_unhide", prompt=[{"text": "Unhide column:", "kind": "cell"}]),
        _m("WINDOW", [
            _m("HORIZONTAL", action="w_window_h"),
            _m("VERTICAL", action="w_window_v"),
            _m("CLEAR", action="w_window_clear"),
            _m("SYNC", action="w_window_sync"),
            _m("UNSYNC", action="w_window_unsync"),
            _m("CHANGE", action="w_window_change"),
        ]),
        _m("TITLES", [
            _m("BOTH", action="w_titles_both", prompt=[{"text": "Titles range:", "kind": "range"}]),
            _m("HORIZONTAL", action="w_titles_h", prompt=[{"text": "Title rows:", "kind": "range"}]),
            _m("VERTICAL", action="w_titles_v", prompt=[{"text": "Title columns:", "kind": "range"}]),
            _m("CLEAR", action="w_titles_clear"),
        ]),
        _m("STATUS", action="w_status"),
        _m("PAGE", action="w_page"),
    ]),
    _m("RANGE", [
        _m("FORMAT", action="r_format", prompt=[{"text": "Format range:", "kind": "range"},
                                               {"text": "Format type:", "kind": "choice",
                                                "choices": ["FIXED", "SCIENTIFIC", "CURRENCY", "PERCENT",
                                                            "COMMA", "GENERAL", "TEXT", "DATE", "TIME", "HIDDEN"]}]),
        _m("ERASE", action="r_erase", prompt=[{"text": "Erase range:", "kind": "range"}]),
        _m("NAME", [
            _m("CREATE", action="r_name_create", prompt=[{"text": "Name:", "kind": "text"},
                                                         {"text": "Range:", "kind": "range"}]),
            _m("DELETE", action="r_name_delete", prompt=[{"text": "Name to delete:", "kind": "text"}]),
            _m("LABELS", action="r_name_labels", prompt=[{"text": "Labels range:", "kind": "range"},
                                                         {"text": "Names position:", "kind": "choice",
                                                          "choices": ["LEFT", "RIGHT", "TOP", "BOTTOM"]}]),
            _m("RESET", action="r_name_reset"),
            _m("TABLE", action="r_name_table"),
        ]),
        _m("JUSTIFY", action="r_justify", prompt=[{"text": "Justify range:", "kind": "range"}]),
        _m("LABEL", action="r_label", prompt=[{"text": "Label range:", "kind": "range"},
                                             {"text": "Alignment:", "kind": "choice",
                                              "choices": ["LEFT", "CENTER", "RIGHT"]}]),
        _m("PROTECT", action="r_protect", prompt=[{"text": "Protect range:", "kind": "range"}]),
        _m("UNPROTECT", action="r_unprotect", prompt=[{"text": "Unprotect range:", "kind": "range"}]),
        _m("INPUT", action="r_input", prompt=[{"text": "Input range:", "kind": "range"}]),
        _m("TRANSPOSE", action="r_transpose", prompt=[{"text": "Source range:", "kind": "range"},
                                                     {"text": "Destination:", "kind": "cell"}]),
        _m("VALUE", action="r_value", prompt=[{"text": "Value range:", "kind": "range"},
                                             {"text": "Destination:", "kind": "cell"}]),
        _m("SEARCH", action="r_search", prompt=[{"text": "Search range:", "kind": "range"},
                                               {"text": "Find what:", "kind": "text"}]),
    ]),
    _m("COPY", action="copy", prompt=[{"text": "Copy from:", "kind": "range"},
                                      {"text": "To:", "kind": "cell"}]),
    _m("MOVE", action="move", prompt=[{"text": "Move from:", "kind": "range"},
                                      {"text": "To:", "kind": "cell"}]),
    _m("FILE", [
        _m("SAVE", action="f_save", prompt=[{"text": "File to save:", "kind": "file", "save": True}]),
        _m("SAVE-AS", action="f_save", prompt=[{"text": "File to save:", "kind": "file", "save": True}]),
        _m("LOAD", action="f_load", prompt=[{"text": "File to load:", "kind": "file"}]),
        _m("COMBINE", [
            _m("COPY", action="f_combine_copy", prompt=[{"text": "File to combine:", "kind": "file"},
                                                        {"text": "Range in file:", "kind": "range"},
                                                        {"text": "To:", "kind": "cell"}]),
            _m("ADD", action="f_combine_add", prompt=[{"text": "File to combine:", "kind": "file"},
                                                      {"text": "Range in file:", "kind": "range"},
                                                      {"text": "To:", "kind": "cell"}]),
            _m("SUBTRACT", action="f_combine_sub", prompt=[{"text": "File to combine:", "kind": "file"},
                                                           {"text": "Range in file:", "kind": "range"},
                                                           {"text": "To:", "kind": "cell"}]),
        ]),
        _m("XTRACT", action="f_xtract", prompt=[{"text": "Extract to file:", "kind": "file", "save": True},
                                                {"text": "Range:", "kind": "range"},
                                                {"text": "Format:", "kind": "choice",
                                                 "choices": ["WK1", "DIF", "TEXT"]}]),
        _m("ERASE", action="f_erase", prompt=[{"text": "Erase file:", "kind": "file", "save": True},
                                             {"text": "Confirm (YES/NO):", "kind": "text"}]),
        _m("LIST", action="f_list", prompt=[{"text": "Directory pattern:", "kind": "text"}]),
        _m("IMPORT", [
            _m("DIF", action="f_import", prompt=[{"text": "Import DIF file:", "kind": "file"},
                                                 {"text": "Place at:", "kind": "cell"}]),
            _m("TEXT", action="f_import_text", prompt=[{"text": "Import text file:", "kind": "file"},
                                                       {"text": "Place at:", "kind": "cell"}]),
            _m("NUMBERS", action="f_import_numbers", prompt=[{"text": "Import numbers file:", "kind": "file"},
                                                             {"text": "Place at:", "kind": "cell"}]),
        ]),
        _m("DIRECTORY", action="f_directory", prompt=[{"text": "Directory:", "kind": "text"}]),
    ]),
    _m("PRINT", [
        _m("PRINTER", action="p_printer", prompt=[{"text": "Print to (device or |command):", "kind": "text"},
                                                  {"text": "Range:", "kind": "range"}]),
        _m("FILE", action="p_file", prompt=[{"text": "Print file name:", "kind": "file", "save": True},
                                            {"text": "Range:", "kind": "range"}]),
        _m("RANGE", action="p_range", prompt=[{"text": "Print range:", "kind": "range"},
                                             {"text": "Destination:", "kind": "choice",
                                              "choices": ["PRINTER", "FILE"]}]),
        _m("LINES-PAGE", action="p_linespage", prompt=[{"text": "Lines per page:", "kind": "number"}]),
        _m("MARGINS", action="p_margins", prompt=[{"text": "Left margin:", "kind": "number"},
                                                 {"text": "Right margin:", "kind": "number"},
                                                 {"text": "Top margin:", "kind": "number"},
                                                 {"text": "Bottom margin:", "kind": "number"}]),
        _m("BORDERS", action="p_borders", prompt=[{"text": "Borders (YES/NO):", "kind": "text"}]),
        _m("HEADER", action="p_header", prompt=[{"text": "Header text:", "kind": "text"}]),
    ]),
    _m("GRAPH", [
        _m("TYPE", [
            _m("LINE", action="g_type_line"),
            _m("BAR", action="g_type_bar"),
            _m("XY", action="g_type_xy"),
            _m("STACKED-BAR", action="g_type_stacked"),
            _m("PIE", action="g_type_pie"),
        ]),
        _m("X-RANGE", action="g_xrange", prompt=[{"text": "X range:", "kind": "range"}]),
        _m("A", action="g_series", prompt=[{"text": "Series A range:", "kind": "range"},
                                           {"text": "Series A name:", "kind": "text"}]),
        _m("B", action="g_series", prompt=[{"text": "Series B range:", "kind": "range"},
                                           {"text": "Series B name:", "kind": "text"}]),
        _m("C", action="g_series", prompt=[{"text": "Series C range:", "kind": "range"},
                                           {"text": "Series C name:", "kind": "text"}]),
        _m("D", action="g_series", prompt=[{"text": "Series D range:", "kind": "range"},
                                           {"text": "Series D name:", "kind": "text"}]),
        _m("E", action="g_series", prompt=[{"text": "Series E range:", "kind": "range"},
                                           {"text": "Series E name:", "kind": "text"}]),
        _m("F", action="g_series", prompt=[{"text": "Series F range:", "kind": "range"},
                                           {"text": "Series F name:", "kind": "text"}]),
        _m("RESET", action="g_reset"),
        _m("VIEW", action="g_view"),
        _m("SAVE", action="g_save", prompt=[{"text": "Graph name:", "kind": "text"}]),
        _m("OPTIONS", [
            _m("LEGEND", action="g_opt_legend", prompt=[{"text": "Legend text:", "kind": "text"}]),
            _m("TITLES", action="g_opt_titles", prompt=[{"text": "Graph title:", "kind": "text"}]),
            _m("GRID", action="g_opt_grid"),
            _m("SCALE", action="g_opt_scale"),
        ]),
    ]),
    _m("DATA", [
        _m("FILL", action="d_fill", prompt=[{"text": "Fill range:", "kind": "range"},
                                            {"text": "Start:", "kind": "number"},
                                            {"text": "Step:", "kind": "number"},
                                            {"text": "Stop:", "kind": "number"}]),
        _m("TABLE", [
            _m("1-VARIABLE", action="d_table1", prompt=[{"text": "Table range:", "kind": "range"},
                                                        {"text": "Input cell:", "kind": "cell"}]),
            _m("2-VARIABLE", action="d_table2", prompt=[{"text": "Table range:", "kind": "range"},
                                                        {"text": "Row input cell:", "kind": "cell"},
                                                        {"text": "Column input cell:", "kind": "cell"}]),
        ]),
        _m("SORT", [
            _m("DATA-RANGE", action="d_sort_range", prompt=[{"text": "Data range:", "kind": "range"}]),
            _m("PRIMARY-KEY", action="d_sort_primary", prompt=[{"text": "Primary key cell:", "kind": "cell"},
                                                              {"text": "Ascending? (YES/NO):", "kind": "text"}]),
            _m("SECONDARY-KEY", action="d_sort_secondary", prompt=[{"text": "Secondary key cell:", "kind": "cell"},
                                                                  {"text": "Ascending? (YES/NO):", "kind": "text"}]),
            _m("RESET", action="d_sort_reset"),
            _m("GO", action="d_sort_go"),
        ]),
        _m("QUERY", [
            _m("INPUT", action="d_query_input", prompt=[{"text": "Input range:", "kind": "range"}]),
            _m("CRITERION", action="d_query_criterion", prompt=[{"text": "Criterion range:", "kind": "range"}]),
            _m("OUTPUT", action="d_query_output", prompt=[{"text": "Output range:", "kind": "range"}]),
            _m("UNIQUE", action="d_query_unique"),
            _m("FIND", action="d_query_find"),
            _m("DELETE", action="d_query_delete"),
            _m("EXTRACT", action="d_query_extract"),
        ]),
        _m("FREQUENCY", action="d_frequency", prompt=[{"text": "Data range:", "kind": "range"},
                                                      {"text": "Bin range:", "kind": "range"}]),
        _m("REGRESSION", action="d_regression", prompt=[{"text": "X range:", "kind": "range"},
                                                        {"text": "Y range:", "kind": "range"},
                                                        {"text": "Output cell:", "kind": "cell"}]),
    ]),
    _m("SYSTEM", action="system", prompt=[{"text": "Command:", "kind": "text"}]),
    _m("ADD-IN", action="addin"),
]

TOP_LABELS = [item.label for item in MENU_TREE]


class MenuState:
    """Pure menu navigation state: stack of (menu_items, index) frames plus
    the highlighted path. screen.py renders from this; app.py mutates it."""

    def __init__(self):
        self.frames: list[tuple[list, int]] = []
        self.active = False

    def open(self):
        self.active = True
        self.frames = [(MENU_TREE, 0)]

    def close(self):
        self.active = False
        self.frames = []

    @property
    def depth(self):
        return len(self.frames)

    def current_items(self):
        return self.frames[-1][0] if self.frames else MENU_TREE

    def current_index(self):
        return self.frames[-1][1]

    def current_item(self) -> MenuItem:
        items, idx = self.frames[-1]
        return items[idx]

    def right(self):
        if not self.frames:
            return
        items, idx = self.frames[0]
        self.frames[0] = (items, (idx + 1) % len(items))
        # keep submenu depth consistent with new top item: drop deeper frames
        if self.depth > 1:
            self.frames = [self.frames[0]]

    def left(self):
        if not self.frames:
            return
        items, idx = self.frames[0]
        self.frames[0] = (items, (idx - 1) % len(items))
        if self.depth > 1:
            self.frames = [self.frames[0]]

    def down(self):
        if not self.frames:
            return
        items, idx = self.frames[-1]
        self.frames[-1] = (items, (idx + 1) % len(items))

    def up(self):
        if not self.frames:
            return
        items, idx = self.frames[-1]
        self.frames[-1] = (items, (idx - 1) % len(items))

    def enter(self):
        """Enter on current item. Returns ('action', item) or ('submenu', item)
        or ('back', None) or ('close', None)."""
        if not self.frames:
            return ("close", None)
        item = self.current_item()
        if item.children:
            idx = 0
            self.frames.append((item.children, idx))
            return ("submenu", item)
        return ("action", item)

    def back(self):
        """Esc/Backspace: one level up; at root closes."""
        if len(self.frames) > 1:
            self.frames.pop()
            return ("back", None)
        self.close()
        return ("close", None)

    def select_letter(self, ch: str):
        """Capital-letter quick select within the current menu level."""
        if not self.frames:
            return None
        items, _ = self.frames[-1]
        for i, item in enumerate(items):
            if item.label.upper().startswith(ch.upper()):
                self.frames[-1] = (items, i)
                return self.enter()
        return None

    def path(self):
        return [self.frames[i][1] for i in range(len(self.frames))]
