"""Shared fixtures: a fake curses module so screen.py / app.py run headless,
plus helpers to drive an App with scripted keys and snapshot the screen."""

from __future__ import annotations

import sys
import types

import pytest


class FakeCursesError(Exception):
    pass


def _make_fake_curses():
    m = types.ModuleType("curses")
    m.error = FakeCursesError
    m.A_REVERSE = 1 << 8
    m.A_BOLD = 1 << 9
    m.A_NORMAL = 0
    m.COLOR_BLACK = 0
    m.COLOR_RED = 1
    m.COLOR_WHITE = 7
    m.COLOR_BLUE = 4
    m.COLOR_CYAN = 6
    m.COLOR_YELLOW = 3
    m.COLORS = 8
    m.KEY_UP = 259
    m.KEY_DOWN = 258
    m.KEY_LEFT = 260
    m.KEY_RIGHT = 261
    m.KEY_HOME = 262
    m.KEY_END = 263
    m.KEY_PPAGE = 339
    m.KEY_NPAGE = 338
    m.KEY_IC = 331
    m.KEY_DC = 330
    m.KEY_BACKSPACE = 263 + 100
    m.KEY_ENTER = 343
    m.KEY_F1 = 265
    m.KEY_F2 = 266
    m.KEY_F3 = 267
    m.KEY_F4 = 268
    m.KEY_F5 = 269
    m.KEY_F6 = 270
    m.KEY_F7 = 271
    m.KEY_F8 = 272
    m.KEY_F9 = 273
    m.KEY_F10 = 274
    m.KEY_F11 = 275
    m.KEY_F12 = 276
    m.KEY_MOUSE = 409
    m.KEY_RESIZE = 410
    m._pairs = {}

    def color_pair(n):
        return 1 << (16 + n)

    def init_pair(n, fg, bg):
        m._pairs[n] = (fg, bg)

    m.color_pair = color_pair
    m.init_pair = init_pair
    m.has_colors = lambda: True
    m.start_color = lambda: None
    m.use_default_colors = lambda: None
    m.curs_set = lambda v: 0
    m.echo = lambda: None
    m.noecho = lambda: None
    m.cbreak = lambda: None
    m.nocbreak = lambda: None
    m.wrapper = lambda fn: None
    return m


class FakeWindow:
    """Minimal curses window: char grid + attr grid + scripted getch queue."""

    def __init__(self, h=25, w=80):
        self.h = h
        self.w = w
        self.grid = [[" "] * w for _ in range(h)]
        self.attrs = [[0] * w for _ in range(h)]
        self.keys = []
        self._timeout = -1

    def getmaxyx(self):
        return (self.h, self.w)

    def addstr(self, y, x, s, attr=0):
        if not (0 <= y < self.h):
            raise FakeCursesError("y out of range")
        for i, ch in enumerate(s):
            xx = x + i
            if 0 <= xx < self.w:
                self.grid[y][xx] = ch
                self.attrs[y][xx] = attr
            elif xx >= self.w:
                raise FakeCursesError("write past right edge")

    def addch(self, y, x, ch, attr=0):
        self.addstr(y, x, ch if isinstance(ch, str) else chr(ch), attr)

    def erase(self):
        self.grid = [[" "] * self.w for _ in range(self.h)]
        self.attrs = [[0] * self.w for _ in range(self.h)]

    clear = erase

    def refresh(self):
        pass

    def keypad(self, flag):
        pass

    def timeout(self, ms):
        self._timeout = ms

    def getch(self):
        if self.keys:
            k = self.keys.pop(0)
            return k
        return -1

    def text(self):
        return ["".join(row) for row in self.grid]

    def snapshot(self):
        return "\n".join(self.text())


@pytest.fixture
def fake_curses(monkeypatch):
    m = _make_fake_curses()
    monkeypatch.setitem(sys.modules, "curses", m)
    # force reimport of modules that bind curses at import time
    for mod in list(sys.modules):
        if mod.startswith("descartes"):
            del sys.modules[mod]
    import descartes.keys  # noqa: F401
    import descartes.screen  # noqa: F401
    return m


@pytest.fixture
def make_app(fake_curses):
    """Factory: App attached to a FakeWindow."""
    from descartes.app import App

    def factory(h=25, w=80, filepath=None):
        win = FakeWindow(h, w)
        app = App(win, filepath)
        app.screen.init()
        return app, win

    return factory


def type_text(app, text):
    from descartes.keys import KeyEvent
    for ch in text:
        app.handle(KeyEvent("char", ch=ch))


def press(app, name):
    from descartes.keys import KeyEvent
    app.handle(KeyEvent("key", name=name))


def char(app, ch):
    from descartes.keys import KeyEvent
    app.handle(KeyEvent("char", ch=ch))


def draw(app):
    app.screen.draw(app)


def screen_text(app, win):
    draw(app)
    return win.snapshot()
