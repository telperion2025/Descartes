"""Key decoding (terminfo quirks, raw escape fallback) and the XTerm mouse
protocol parser (SGR 1006 with 1000 fallback). This module and screen.py are
the only ones allowed to import curses.
"""

from __future__ import annotations

import curses

try:
    _ESCDELAY_DEFAULT = 25
except Exception:  # pragma: no cover
    _ESCDELAY_DEFAULT = 25


class KeyEvent:
    """Normalized key event. kind: 'char' | 'key' | 'mouse' | 'paste' | 'resize'."""

    __slots__ = ("kind", "name", "ch", "mouse")

    def __init__(self, kind, name=None, ch=None, mouse=None):
        self.kind = kind
        self.name = name
        self.ch = ch
        self.mouse = mouse

    def __repr__(self):  # pragma: no cover
        if self.kind == "char":
            return f"KeyEvent(char={self.ch!r})"
        if self.kind == "mouse":
            return f"KeyEvent(mouse={self.mouse!r})"
        return f"KeyEvent({self.name})"


class MouseEvent:
    __slots__ = ("x", "y", "button", "press", "release", "click_count",
                 "drag", "wheel_up", "wheel_down", "modifiers")

    def __init__(self, x, y, button=0, press=True, release=False, click_count=1,
                 drag=False, wheel_up=False, wheel_down=False, modifiers=frozenset()):
        self.x = x
        self.y = y
        self.button = button
        self.press = press
        self.release = release
        self.click_count = click_count
        self.drag = drag
        self.wheel_up = wheel_up
        self.wheel_down = wheel_down
        self.modifiers = set(modifiers)

    def __repr__(self):  # pragma: no cover
        return (f"MouseEvent(x={self.x},y={self.y},btn={self.button},press={self.press},"
                f"release={self.release},clicks={self.click_count},drag={self.drag},"
                f"wheel={'up' if self.wheel_up else 'down' if self.wheel_down else '-'},"
                f"mod={sorted(self.modifiers)})")


# ---------------------------------------------------------------------------
# Mouse protocol
# ---------------------------------------------------------------------------

MOUSE_ENABLE = "\x1b[?1000h\x1b[?1006h"
MOUSE_DISABLE = "\x1b[?1006l\x1b[?1000l"


class MouseParser:
    """Incremental parser for XTerm mouse bytes (SGR 1006 and legacy 1000).

    Feed bytes; returns a MouseEvent when a complete report arrives, else None.
    Bytes that do not start a mouse sequence are returned via `drain_junk`
    so they can be re-processed as keyboard input (never leak into cell entry
    as mouse bytes: they are returned separately).
    """

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        """Feed raw bytes; returns (events, leftover_non_mouse_bytes)."""
        events = []
        junk = bytearray()
        self.buf += data
        i = 0
        buf = self.buf
        while i < len(buf):
            if buf[i] == 0x1B and i + 1 < len(buf) and buf[i + 1] == ord("["):
                # find terminator
                j = i + 2
                if j < len(buf) and buf[j] == ord("<"):
                    j += 1
                    end = j
                    while end < len(buf) and buf[end] not in (ord("M"), ord("m")):
                        end += 1
                    if end >= len(buf):
                        break  # incomplete; wait for more bytes
                    ev = _parse_sgr(bytes(buf[j:end]), chr(buf[end]))
                    if ev is not None:
                        events.append(ev)
                    i = end + 1
                    continue
                else:
                    # legacy 1000: ESC [ M Cb Cx Cy (6 bytes total)
                    if i + 5 < len(buf) and buf[i + 2] == ord("M"):
                        ev = _parse_legacy(bytes(buf[i + 3:i + 6]))
                        if ev is not None:
                            events.append(ev)
                        i += 6
                        continue
                    break  # incomplete escape sequence
            else:
                junk.append(buf[i])
                i += 1
        self.buf = buf[i:]
        return events, bytes(junk)


def _parse_sgr(body: bytes, final: str):
    try:
        parts = body.decode("ascii").split(";")
        cb, x, y = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return None
    x -= 1
    y -= 1
    press = final == "M"
    button = cb & 0x03
    wheel_up = (cb & 0x40) and button == 0
    wheel_down = (cb & 0x40) and button == 1
    drag = bool(cb & 0x20)
    shift = bool(cb & 0x04)
    meta = bool(cb & 0x08)
    ctrl = bool(cb & 0x10)
    modifiers = set()
    if shift:
        modifiers.add("shift")
    if meta:
        modifiers.add("alt")
    if ctrl:
        modifiers.add("ctrl")
    if wheel_up or wheel_down:
        return MouseEvent(x, y, button=button, press=press, release=not press,
                          wheel_up=wheel_up, wheel_down=wheel_down, modifiers=modifiers)
    if button == 3 and not press:
        return None  # release of non-button (SGR sends 'm' for all releases)
    return MouseEvent(x, y, button=button, press=press, release=not press,
                      drag=drag, modifiers=modifiers)


def _parse_legacy(b: bytes):
    if len(b) < 3:
        return None
    cb, cx, cy = b[0] - 32, b[1] - 32, b[2] - 32
    if cb < 0 or cx < 1 or cy < 1:
        return None
    button = cb & 0x03
    wheel_up = bool(cb & 0x40) and button == 0
    wheel_down = bool(cb & 0x40) and button == 1
    press = not (cb & 0x20) or True
    return MouseEvent(cx - 1, cy - 1, button=button, press=press,
                      wheel_up=wheel_up, wheel_down=wheel_down,
                      drag=bool(cb & 0x20),
                      modifiers={"shift"} if cb & 4 else set())


# ---------------------------------------------------------------------------
# Key reading
# ---------------------------------------------------------------------------

# Raw escape-sequence fallbacks for terminals whose terminfo lacks the keys.
_RAW_SEQUENCES = {
    "\x1bOP": "F1", "\x1bOQ": "F2", "\x1bOR": "F3", "\x1bOS": "F4",
    "\x1b[11~": "F1", "\x1b[12~": "F2", "\x1b[13~": "F3", "\x1b[14~": "F4",
    "\x1b[15~": "F5", "\x1b[17~": "F6", "\x1b[18~": "F7", "\x1b[19~": "F8",
    "\x1b[20~": "F9", "\x1b[21~": "F10", "\x1b[23~": "F11", "\x1b[24~": "F12",
    "\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT",
    "\x1b[H": "HOME", "\x1b[F": "END",
    "\x1b[5~": "PGUP", "\x1b[6~": "PGDN",
    "\x1b[2~": "INS", "\x1b[3~": "DEL",
    "\x1bOA": "UP", "\x1bOB": "DOWN", "\x1bOC": "RIGHT", "\x1bOD": "LEFT",
    "\x1bOH": "HOME", "\x1bOF": "END",
    "\x1b[1;5D": "CTRL_LEFT", "\x1b[1;5C": "CTRL_RIGHT",
    "\x1b[1;5A": "CTRL_UP", "\x1b[1;5B": "CTRL_DOWN",
    "\x1b[5D": "CTRL_LEFT", "\x1b[5C": "CTRL_RIGHT",
}

_KEY_NAMES = {
    curses.KEY_UP: "UP", curses.KEY_DOWN: "DOWN", curses.KEY_LEFT: "LEFT",
    curses.KEY_RIGHT: "RIGHT", curses.KEY_HOME: "HOME", curses.KEY_END: "END",
    curses.KEY_PPAGE: "PGUP", curses.KEY_NPAGE: "PGDN", curses.KEY_IC: "INS",
    curses.KEY_DC: "DEL", curses.KEY_BACKSPACE: "BACKSPACE",
    curses.KEY_ENTER: "ENTER", 10: "ENTER", 13: "ENTER",
    curses.KEY_F1: "F1", curses.KEY_F2: "F2", curses.KEY_F3: "F3",
    curses.KEY_F4: "F4", curses.KEY_F5: "F5", curses.KEY_F6: "F6",
    curses.KEY_F7: "F7", curses.KEY_F8: "F8", curses.KEY_F9: "F9",
    curses.KEY_F10: "F10", curses.KEY_F11: "F11", curses.KEY_F12: "F12",
    27: "ESC", 9: "TAB", 127: "BACKSPACE", 8: "BACKSPACE",
    curses.KEY_MOUSE: "MOUSE",
    curses.KEY_RESIZE: "RESIZE",
}

_BACKSPACE_CODES = (curses.KEY_BACKSPACE, 127, 8)


def decode_code(code: int) -> str | None:
    """Map a curses getch code to a key name (None = plain/control char)."""
    if code in _KEY_NAMES:
        return _KEY_NAMES[code]
    if 0 <= code < 32:
        return None   # control char (Ctrl+C = 3, Ctrl+Q = 17, ...) -> char event
    if 32 <= code < 256:
        return None   # printable / latin-1 char
    return f"KEY{code}"


def read_event(stdscr, escdelay_ms: int = 25) -> KeyEvent | None:
    """Read one event from stdscr.

    Handles curses key codes, raw escape fallbacks (incl. \\x1bOP F1..F4),
    Alt+key (ESC prefix), and XTerm mouse reports parsed manually so SGR 1006
    works even when ncurses does not decode it as KEY_MOUSE.
    """
    code = stdscr.getch()
    if code == -1:
        return None
    if code == 27:
        # collect the escape sequence with a short timeout (ESCDELAY)
        seq = bytearray()
        stdscr.timeout(escdelay_ms)
        try:
            while len(seq) < 16:
                c = stdscr.getch()
                if c == -1:
                    break
                seq.append(c & 0xFF)
                s = bytes(seq).decode("latin-1")
                if s in _RAW_SEQUENCES:
                    break
                if len(seq) >= 3 and seq[0] == ord("[") and seq[1] == ord("<") \
                        and seq[-1] in (ord("M"), ord("m")):
                    break  # complete SGR mouse report
                if len(seq) >= 3 and seq[0] == ord("[") and seq[-1] == ord("~"):
                    break  # complete CSI ~ sequence
                if len(seq) == 6 and seq[0] == ord("[") and seq[1] == ord("M"):
                    break  # complete legacy mouse report
        finally:
            stdscr.timeout(-1)
        if not seq:
            return KeyEvent("key", name="ESC")   # lone ESC
        text = "\x1b" + bytes(seq).decode("latin-1")
        parser = MouseParser()
        events, junk = parser.feed(text.encode("latin-1"))
        if events:
            return KeyEvent("mouse", mouse=events[0])
        name = _RAW_SEQUENCES.get(text)
        if name:
            return KeyEvent("key", name=name)
        if junk and junk[0] >= 32:
            if len(junk) == 1:
                ch = junk.decode("latin-1")
                return KeyEvent("char", ch=ch, name="ALT+" + ch)
            return KeyEvent("key", name="ESC")
        return KeyEvent("key", name="ESC")
        return KeyEvent("key", name="ESC")
    name = decode_code(code)
    if name is None:
        try:
            ch = chr(code)
        except ValueError:
            return KeyEvent("key", name=f"KEY{code}")
        return KeyEvent("char", ch=ch)
    if name == "BACKSPACE":
        return KeyEvent("key", name="BACKSPACE")
    return KeyEvent("key", name=name)
