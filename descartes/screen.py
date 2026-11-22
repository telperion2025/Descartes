"""All curses drawing. This module and keys.py are the only ones that import
curses. Fixed 80-column layout (grid area extends on wider terminals):

  row 0  (1)  menu bar
  row 1  (2)  control panel: "A1  READY" left, prompt right
  row 2  (3)  cell content line (raw text of the highlighted cell)
  row 3  (4)  column headers A B C ...
  rows 4..21 (5..22)  worksheet, 7-char row gutter
  row 22 (23) indicator line: CAPS NUM SCROLL CALC MEM OVR  + clock
  row 23 (24) message line
  row 24 (25) error line
"""

from __future__ import annotations

import curses
import datetime

from . import fmt as _fmt
from .grid import (MAX_COLS, col_letters, format_address, normalize_corners,
                   range_cells)
from .menus import MENU_TREE, TOP_LABELS

ROW_MENUBAR = 0
ROW_CONTROL = 1
ROW_CONTENT = 2
ROW_HEADERS = 3
ROW_GRID0 = 4
ROW_GRID1 = 21
ROW_INDICATOR = 22
ROW_MESSAGE = 23
ROW_ERROR = 24
GUTTER_W = 7
MIN_W, MIN_H = 80, 25

# color pair ids (spec 5.1)
P_BG = 1        # white / blue   worksheet background
P_MENU = 2      # black / cyan   menu bar text, dropdowns
P_HILITE = 3    # black / yellow highlighted menu item / cursor in dialogs
P_HEAD = 4      # white / cyan   column headers, row gutter
P_HEAD_HI = 5   # black / cyan   highlighted column header / row
P_ERROR = 6     # red / white    error messages
P_CURSOR = 7    # bright-yellow / blue  cursor cell


class TerminalTooSmall(Exception):
    def __init__(self, w, h):
        super().__init__(f"Terminal must be at least 80x25, current {w}x{h}")
        self.w = w
        self.h = h


class Screen:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.has_color = False
        self.attrs = {}
        self.utf8 = True

    # -- setup --------------------------------------------------------------

    def init(self):
        h, w = self.stdscr.getmaxyx()
        if w < MIN_W or h < MIN_H:
            raise TerminalTooSmall(w, h)
        try:
            self.utf8 = curses.curs_set(0) is not None or True
        except curses.error:
            pass
        curses.curs_set(0)
        self.stdscr.keypad(True)
        self.has_color = curses.has_colors()
        if self.has_color:
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLUE
            def pair(pid, fg, bgc):
                try:
                    curses.init_pair(pid, fg, bgc)
                    self.attrs[pid] = curses.color_pair(pid)
                except curses.error:
                    self.attrs[pid] = curses.A_REVERSE
            pair(P_BG, curses.COLOR_WHITE, curses.COLOR_BLUE if bg == -1 else bg)
            pair(P_MENU, curses.COLOR_BLACK, curses.COLOR_CYAN)
            pair(P_HILITE, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            pair(P_HEAD, curses.COLOR_WHITE, curses.COLOR_CYAN)
            pair(P_HEAD_HI, curses.COLOR_BLACK, curses.COLOR_CYAN)
            pair(P_ERROR, curses.COLOR_RED, curses.COLOR_WHITE)
            pair(P_CURSOR, curses.COLOR_YELLOW, curses.COLOR_BLUE if bg == -1 else bg)
        else:
            self.attrs = {pid: 0 for pid in (P_BG, P_MENU, P_HILITE, P_HEAD,
                                            P_HEAD_HI, P_ERROR, P_CURSOR)}
        if not self.has_color:
            # mono terminals use A_REVERSE for pairs 2, 3, 5, 7
            self.attrs[P_MENU] = curses.A_REVERSE
            self.attrs[P_HILITE] = curses.A_REVERSE | curses.A_BOLD
            self.attrs[P_HEAD] = 0
            self.attrs[P_HEAD_HI] = curses.A_REVERSE
            self.attrs[P_ERROR] = curses.A_REVERSE
            self.attrs[P_CURSOR] = curses.A_REVERSE | curses.A_BOLD
        self.attrs["bold"] = curses.A_BOLD
        self.attrs["plain"] = 0

    def size(self):
        return self.stdscr.getmaxyx()

    def a(self, pid):
        return self.attrs.get(pid, 0)

    # -- primitives ---------------------------------------------------------

    def put(self, y, x, text, attr=0):
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        text = text[: max(0, w - x)]
        if not text:
            return
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            # writing to the bottom-right corner raises on some terminals
            if y == h - 1 and x + len(text) == w:
                try:
                    self.stdscr.addstr(y, x, text[:-1], attr)
                except curses.error:
                    pass

    def hline(self, y, x, n, ch="-", attr=0):
        self.put(y, x, ch * n, attr)

    def box(self, y0, x0, h, w, attr=0):
        """ASCII box (ACS where available would be used by real terminals;
        ASCII keeps snapshots portable)."""
        self.put(y0, x0, "+" + "-" * (w - 2) + "+", attr)
        for y in range(y0 + 1, y0 + h - 1):
            self.put(y, x0, "|", attr)
            self.put(y, x0 + w - 1, "|", attr)
        self.put(y0 + h - 1, x0, "+" + "-" * (w - 2) + "+", attr)

    def clear(self):
        self.stdscr.erase()

    def refresh(self):
        self.stdscr.refresh()

    # -- top-level ----------------------------------------------------------

    def draw(self, app):
        self.clear()
        if app.mode == "MENU":
            self.draw_menubar(app, active=True)
        else:
            self.draw_menubar(app, active=False)
        self.draw_control(app)
        self.draw_content_line(app)
        self.draw_headers(app)
        self.draw_grid(app)
        self.draw_indicators(app)
        self.draw_message(app)
        if app.mode == "MENU":
            self.draw_menus(app)
        elif app.mode in ("VALUE", "LABEL", "EDIT", "POINT", "DIALOG", "ERROR"):
            pass  # control/content lines already show the entry
        if app.mode == "GRAPH":
            self.draw_graph_view(app)
        if app.mode == "SYSTEM":
            self.draw_system_view(app)
        if app.mode == "HELP":
            self.draw_help(app)
        self.refresh()

    # -- pieces -------------------------------------------------------------

    def draw_menubar(self, app, active):
        attr = self.a(P_MENU) if active else self.a(P_BG)
        self.put(ROW_MENUBAR, 0, " " * self.size()[1], self.a(P_BG) if not active else self.a(P_MENU))
        if active:
            x = 1
            for i, label in enumerate(TOP_LABELS):
                is_cur = app.menu.depth >= 1 and app.menu.frames[0][1] == i
                a = self.a(P_HILITE) if is_cur else self.a(P_MENU)
                self.put(ROW_MENUBAR, x, " " + label + " ", a)
                x += len(label) + 2
        else:
            self.put(ROW_MENUBAR, 1, "Descartes 1-2-3", self.a(P_BG))

    def draw_control(self, app):
        addr = format_address(app.cur[0], app.cur[1])
        mode = app.mode
        left = f"{addr}  {mode}"
        self.put(ROW_CONTROL, 0, left.ljust(30), self.a(P_BG))
        right = ""
        if app.mode in ("VALUE", "LABEL", "EDIT"):
            right = app.entry
        elif app.mode == "POINT":
            right = app.prompt_text + " " + app.entry
        elif app.mode == "DIALOG":
            right = app.prompt_text + " " + app.entry
        elif app.mode == "MENU":
            item = app.menu.current_item() if app.menu.frames else None
            right = (item.label if item else "") + "..."
        elif app.mode == "ERROR":
            right = app.error
        w = self.size()[1]
        self.put(ROW_CONTROL, 30, right[: max(0, w - 30)], self.a(P_BG))

    def draw_content_line(self, app):
        w = self.size()[1]
        if app.mode in ("VALUE", "LABEL", "EDIT"):
            text = app.entry
        elif app.mode == "POINT" or app.mode == "DIALOG":
            text = app.entry
        else:
            cell = app.sheet.cell(*app.cur)
            text = cell.raw if cell is not None and cell.raw is not None else ""
            if cell is not None and cell.hidden and cell.kind == "FORMULA":
                text = "*****"
        self.put(ROW_CONTENT, 0, text[:w], self.a(P_BG))

    def _col_x(self, app, c):
        x = GUTTER_W
        tc = app.top[0]
        for cc in range(tc, c):
            x += app.sheet.col_width(cc)
        return x

    def draw_headers(self, app):
        w = self.size()[1]
        self.put(ROW_HEADERS, 0, " " * GUTTER_W, self.a(P_HEAD))
        c = app.top[0]
        x = GUTTER_W
        while x < w and c < MAX_COLS:
            cw = app.sheet.col_width(c)
            letters = col_letters(c)
            label = letters.center(cw)[:cw]
            a = self.a(P_HEAD_HI) if c == app.cur[0] else self.a(P_HEAD)
            self.put(ROW_HEADERS, x, label, a)
            x += cw
            c += 1

    def draw_grid(self, app):
        h, w = self.size()
        bg = self.a(P_BG)
        # gutter + rows
        nrows = ROW_GRID1 - ROW_GRID0 + 1
        tr = app.top[1]
        for i in range(nrows):
            y = ROW_GRID0 + i
            r = tr + i
            if r >= 8192:
                self.put(y, 0, " " * w, bg)
                continue
            row_hi = r == app.cur[1]
            gattr = self.a(P_HEAD_HI) if row_hi else self.a(P_HEAD)
            self.put(y, 0, f"{r+1:>6}|", gattr)
            c = app.top[0]
            x = GUTTER_W
            while x < w and c < MAX_COLS:
                cw = app.sheet.col_width(c)
                cell = app.sheet.cell(c, r)
                text, align = self._cell_text(app, cell, c, r)
                if align == "right":
                    text = text.rjust(cw)
                elif align == "center":
                    text = text.center(cw)
                else:
                    text = text.ljust(cw)
                a = bg
                if (c, r) == app.cur:
                    a = self.a(P_CURSOR)
                elif app.mode == "POINT" and app.point_range and \
                        _in_range(app.point_range, c, r):
                    a = self.a(P_HILITE)
                self.put(y, x, text[:cw], a)
                x += cw
                c += 1
            if x < w:
                self.put(y, x, " " * (w - x), bg)

    def _cell_text(self, app, cell, c, r):
        """-> (display text, alignment)"""
        if cell is None or cell.kind == "EMPTY":
            return "", "left"
        fmt = cell.fmt or app.sheet.global_fmt
        if cell.hidden and cell.kind == "FORMULA":
            return "*****", "left"
        text = _fmt.display(cell.value, fmt)
        if app.sheet.zero_suppress and isinstance(cell.value, (int, float)) and cell.value == 0:
            text = ""
        if cell.kind == "LABEL":
            return text, cell.align or "left"
        if isinstance(cell.value, str):
            return text, "left"
        return text, "right"

    def draw_indicators(self, app):
        w = self.size()[1]
        inds = []
        inds.append("CAPS" if app.caps else "    ")
        inds.append("NUM" if app.numlock else "   ")
        inds.append("SCRL" if app.scroll_lock else "    ")
        inds.append("CALC" if app.engine.needs_calc() else "    ")
        inds.append("MEM" if False else "   ")
        inds.append("OVR" if app.overwrite else "INS")
        left = " ".join(inds)
        now = datetime.datetime.now()
        clock = now.strftime("%H:%M:%S")
        self.put(ROW_INDICATOR, 0, " " * w, self.a(P_BG))
        self.put(ROW_INDICATOR, 0, left, self.a(P_BG))
        self.put(ROW_INDICATOR, w - 9, clock, self.a(P_BG))

    def draw_message(self, app):
        w = self.size()[1]
        self.put(ROW_MESSAGE, 0, (app.message or "")[:w], self.a(P_BG))
        err = app.error or ""
        self.put(ROW_ERROR, 0, err[:w], self.a(P_ERROR) if err else self.a(P_BG))

    # -- menus --------------------------------------------------------------

    def draw_menus(self, app):
        menu = app.menu
        if not menu.frames:
            return
        # top-level bar highlight
        for i, label in enumerate(TOP_LABELS):
            is_cur = menu.frames[0][1] == i
            x = 1 + sum(len(l) + 2 for l in TOP_LABELS[:i])
            self.put(ROW_MENUBAR, x, " " + label + " ",
                     self.a(P_HILITE) if is_cur else self.a(P_MENU))
        # dropdowns: one box per open frame, cascading right
        x = 1 + sum(len(l) + 2 for l in TOP_LABELS[:menu.frames[0][1]])
        y = ROW_MENUBAR + 1
        for depth, (items, idx) in enumerate(menu.frames[1:], start=1):
            wbox = max(len(it.label) for it in items) + 2
            hbox = len(items) + 2
            self.box(y, x, hbox, wbox, self.a(P_MENU))
            for i, it in enumerate(items):
                a = self.a(P_HILITE) if i == idx else self.a(P_MENU)
                self.put(y + 1 + i, x + 1, it.label.ljust(wbox - 2), a)
            x += wbox - 1  # border overlap
            y += 1

    # -- graph / system / help overlays -------------------------------------

    def draw_graph_view(self, app):
        h, w = self.size()
        lines = app.graph_lines or []
        self.put(ROW_MENUBAR, 0, " GRAPH VIEW - any key returns ".ljust(w), self.a(P_MENU))
        for i, ln in enumerate(lines):
            if ROW_GRID0 + i > ROW_GRID1:
                break
            self.put(ROW_GRID0 + i, 0, ln[:w], self.a(P_BG))

    def draw_system_view(self, app):
        h, w = self.size()
        self.put(ROW_MENUBAR, 0, " SYSTEM - any key returns ".ljust(w), self.a(P_MENU))
        lines = app.system_lines or []
        start = app.system_scroll
        for i in range(ROW_GRID0, ROW_GRID1 + 1):
            idx = start + (i - ROW_GRID0)
            if 0 <= idx < len(lines):
                self.put(i, 0, lines[idx][:w], self.a(P_BG))

    def draw_help(self, app):
        h, w = self.size()
        self.put(ROW_MENUBAR, 0, " HELP - any key returns ".ljust(w), self.a(P_MENU))
        from .app import HELP_TEXT
        for i, ln in enumerate(HELP_TEXT):
            if ROW_GRID0 + i > ROW_GRID1:
                break
            self.put(ROW_GRID0 + i, 0, ln[:w], self.a(P_BG))


def _in_range(rng, c, r):
    (c1, r1), (c2, r2) = rng
    return c1 <= c <= c2 and r1 <= r <= r2
