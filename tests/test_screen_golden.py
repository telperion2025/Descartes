"""Golden snapshot tests: drawing commands snapshot to text and compare."""

import os
import re

import pytest

from conftest import screen_text, type_text, press

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")


def _normalize(snap):
    """Mask the clock on the indicator line (row 23, right-aligned)."""
    lines = snap.split("\n")
    lines[22] = re.sub(r"\d{2}:\d{2}:\d{2}\s*$", "<CLOCK>", lines[22])
    return "\n".join(lines)


def _golden_check(snap, name):
    snap = _normalize(snap)
    path = os.path.join(GOLDEN_DIR, name)
    if os.environ.get("REGEN_GOLDEN"):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(snap + "\n")
    with open(path) as f:
        assert snap == f.read().rstrip("\n")


def test_ready_screen_golden(make_app):
    app, win = make_app()
    snap = screen_text(app, win)
    _golden_check(snap, "ready.txt")


def test_menu_open_golden(make_app):
    from conftest import char
    app, win = make_app()
    char(app, "/")
    press(app, "ENTER")   # open WORKSHEET dropdown
    press(app, "ENTER")   # open GLOBAL submenu
    snap = screen_text(app, win)
    lines = snap.split("\n")
    assert "WORKSHEET" in lines[0]
    assert "GLOBAL" in lines[2]      # dropdown starts one row below menu bar
    assert "RECALCULATION" in snap
    _golden_check(snap, "menu.txt")


def test_data_entry_golden(make_app):
    app, win = make_app()
    type_text(app, "123")
    press(app, "ENTER")
    type_text(app, "@SUM(A1..A1)*2")
    snap = screen_text(app, win)   # entry visible on control/content lines
    lines = snap.split("\n")
    assert "VALUE" in lines[1]
    assert "@SUM(A1..A1)*2" in lines[1] or "@SUM(A1..A1)*2" in lines[2]
    press(app, "ENTER")
    snap = screen_text(app, win)
    assert "      246" in snap.split("\n")[5]
