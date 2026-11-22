import pytest

from descartes.keys import MouseParser, decode_code
from descartes.menus import MENU_TREE, TOP_LABELS, MenuState


class TestMouseParser:
    def test_sgr_press_release(self):
        p = MouseParser()
        evs, junk = p.feed(b"\x1b[<0;10;5M")
        assert junk == b""
        assert len(evs) == 1
        e = evs[0]
        assert (e.x, e.y) == (9, 4) and e.button == 0 and e.press and not e.release
        evs, _ = p.feed(b"\x1b[<0;10;5m")
        assert evs[0].release and not evs[0].press

    def test_wheel(self):
        p = MouseParser()
        evs, _ = p.feed(b"\x1b[<64;1;1M")
        assert evs[0].wheel_up
        evs, _ = p.feed(b"\x1b[<65;1;1M")
        assert evs[0].wheel_down

    def test_modifiers_and_drag(self):
        p = MouseParser()
        evs, _ = p.feed(b"\x1b[<48;3;3M")  # cb=48 = 0x30: drag (0x20) + ctrl (0x10), button 0
        assert evs[0].drag and "ctrl" in evs[0].modifiers

    def test_legacy_1000(self):
        p = MouseParser()
        # ESC [ M cb cx cy with cb=32+' '=0x20 -> button 0 press at (4,2)
        evs, _ = p.feed(b"\x1b[M" + bytes([32 + 0, 32 + 5, 32 + 3]))
        assert len(evs) == 1 and (evs[0].x, evs[0].y) == (4, 2)

    def test_junk_not_mouse(self):
        p = MouseParser()
        evs, junk = p.feed(b"abc")
        assert evs == [] and junk == b"abc"

    def test_split_across_feeds(self):
        p = MouseParser()
        evs, _ = p.feed(b"\x1b[<0;10")
        assert evs == []
        evs, _ = p.feed(b";5M")
        assert len(evs) == 1

    def test_decode_code(self):
        import curses
        assert decode_code(curses.KEY_UP) == "UP"
        assert decode_code(ord("a")) is None
        assert decode_code(127) == "BACKSPACE"


class TestMenuState:
    def test_top_labels(self):
        assert TOP_LABELS == ["WORKSHEET", "RANGE", "COPY", "MOVE", "FILE",
                              "PRINT", "GRAPH", "DATA", "SYSTEM", "ADD-IN"]

    def test_navigation(self):
        m = MenuState()
        m.open()
        assert m.depth == 1
        m.right()
        assert m.current_item().label == "RANGE"
        m.enter()  # submenu
        assert m.depth == 2
        assert m.current_item().label == "FORMAT"
        m.down()
        assert m.current_item().label == "ERASE"
        r = m.back()
        assert r[0] == "back" and m.depth == 1
        r = m.back()
        assert r[0] == "close" and not m.active

    def test_letter_select_action(self):
        m = MenuState()
        m.open()
        m.right(); m.right()  # COPY
        r = m.select_letter("C")
        assert r[0] == "action" and r[1].action == "copy"

    def test_enter_leaf_returns_action(self):
        m = MenuState()
        m.open()
        # navigate to SYSTEM (leaf at top level, index 8)
        for _ in range(8):
            m.right()
        assert m.current_item().label == "SYSTEM"
        r = m.enter()
        assert r[0] == "action" and r[1].action == "system"

    def test_full_tree_wellformed(self):
        def walk(items):
            for it in items:
                assert it.label
                if it.children is not None:
                    walk(it.children)
                else:
                    assert it.action, f"leaf without action: {it.label}"
        walk(MENU_TREE)
