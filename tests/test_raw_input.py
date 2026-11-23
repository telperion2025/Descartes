"""Brute-force raw input (RawReader over a pipe) and reverse-video headers."""

import os

from conftest import draw
from descartes.keys import RawReader


def _reader(escdelay_ms=5):
    r, w = os.pipe()
    return RawReader(r, escdelay_ms=escdelay_ms), w, r


def _send(w, data):
    os.write(w, data)


class TestRawReaderMouse:
    def test_sgr_press(self):
        rd, w, r = _reader()
        _send(w, b"\x1b[<0;10;5M")
        ev = rd.read_event()
        assert ev.kind == "mouse"
        assert (ev.mouse.x, ev.mouse.y, ev.mouse.button) == (9, 4, 0)
        assert ev.mouse.press and not ev.mouse.release
        os.close(w); os.close(r)

    def test_sgr_release_and_wheel(self):
        rd, w, r = _reader()
        _send(w, b"\x1b[<0;3;3m")          # release
        ev = rd.read_event()
        assert ev.kind == "mouse" and ev.mouse.release
        _send(w, b"\x1b[<64;1;1M")         # wheel up
        ev = rd.read_event()
        assert ev.kind == "mouse" and ev.mouse.wheel_up
        _send(w, b"\x1b[<65;1;1M")         # wheel down
        ev = rd.read_event()
        assert ev.kind == "mouse" and ev.mouse.wheel_down
        os.close(w); os.close(r)

    def test_legacy_1000(self):
        rd, w, r = _reader()
        _send(w, b"\x1b[M" + bytes([32 + 0, 32 + 10, 32 + 6]))   # cb=0, cx=10, cy=6
        ev = rd.read_event()
        assert ev.kind == "mouse"
        assert (ev.mouse.x, ev.mouse.y, ev.mouse.button) == (9, 5, 0)
        os.close(w); os.close(r)

    def test_mouse_bytes_never_leak_as_chars(self):
        rd, w, r = _reader()
        _send(w, b"\x1b[<0;10;5Mx")        # mouse click then 'x'
        ev1 = rd.read_event()
        ev2 = rd.read_event()
        assert ev1.kind == "mouse"
        assert ev2.kind == "char" and ev2.ch == "x"
        os.close(w); os.close(r)


class TestRawReaderKeys:
    def test_arrow_and_function_keys(self):
        rd, w, r = _reader()
        for seq, name in [(b"\x1b[A", "UP"), (b"\x1b[B", "DOWN"), (b"\x1b[C", "RIGHT"),
                          (b"\x1b[D", "LEFT"), (b"\x1b[5~", "PGUP"), (b"\x1b[6~", "PGDN"),
                          (b"\x1b[5;5~", "CTRL_PGUP"), (b"\x1bOP", "F1"), (b"\x1b[15~", "F5"),
                          (b"\x1b[H", "HOME"), (b"\x1b[F", "END"), (b"\x1b[3~", "DEL")]:
            _send(w, seq)
            ev = rd.read_event()
            assert ev.kind == "key" and ev.name == name, (seq, ev)
        os.close(w); os.close(r)

    def test_split_sequence_across_reads(self):
        rd, w, r = _reader()
        _send(w, b"\x1b[")
        _send(w, b"A")
        ev = rd.read_event()
        assert ev.kind == "key" and ev.name == "UP"
        os.close(w); os.close(r)

    def test_lone_esc_after_timeout(self):
        rd, w, r = _reader(escdelay_ms=5)
        _send(w, b"\x1b")
        ev = rd.read_event()
        assert ev.kind == "key" and ev.name == "ESC"
        os.close(w); os.close(r)

    def test_alt_key(self):
        rd, w, r = _reader(escdelay_ms=5)
        _send(w, b"\x1bq")
        ev = rd.read_event()
        assert ev.kind == "char" and ev.ch == "q" and ev.name == "ALT+q"
        os.close(w); os.close(r)

    def test_control_and_enter(self):
        rd, w, r = _reader()
        _send(w, b"\x03")
        ev = rd.read_event()
        assert ev.kind == "char" and ev.ch == "\x03"
        _send(w, b"\r")
        ev = rd.read_event()
        assert ev.kind == "key" and ev.name == "ENTER"
        _send(w, b"\x7f")
        ev = rd.read_event()
        assert ev.kind == "key" and ev.name == "BACKSPACE"
        os.close(w); os.close(r)

    def test_utf8_char(self):
        rd, w, r = _reader()
        _send(w, "é".encode("utf-8"))
        ev = rd.read_event()
        assert ev.kind == "char" and ev.ch == "é"
        os.close(w); os.close(r)

    def test_resize_detection(self, monkeypatch):
        rd, w, r = _reader()
        rd._last_size = (80, 25)
        import descartes.keys as keys_mod
        monkeypatch.setattr(keys_mod.os, "get_terminal_size",
                            lambda fd: type("S", (), {"columns": 100, "lines": 40})())
        ev = rd.read_event()
        assert ev is not None and ev.kind == "key" and ev.name == "RESIZE"
        os.close(w); os.close(r)


class TestReverseVideoHeaders:
    def test_header_pairs_reversed(self, make_app):
        import curses
        app, win = make_app()
        assert curses._pairs[4] == (curses.COLOR_BLACK, curses.COLOR_WHITE)
        assert curses._pairs[5] == (curses.COLOR_BLACK, curses.COLOR_WHITE)
        draw(app)
        from descartes.screen import ROW_HEADERS, P_HEAD, P_HEAD_HI
        assert win.attrs[ROW_HEADERS][7 + 9] == app.screen.a(P_HEAD)      # column B header
        assert win.attrs[ROW_HEADERS][7] == app.screen.a(P_HEAD_HI)       # current column A
        assert win.attrs[4][0] == app.screen.a(P_HEAD_HI)   # current row gutter
        assert win.attrs[4][7] == app.screen.a(7)           # cursor cell
