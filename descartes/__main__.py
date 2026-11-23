"""Entry point: python3 -m descartes [file.vg1]

Sets ESCDELAY before initscr (read by both Linux/glibc and macOS/BSD curses),
enables the XTerm mouse protocol, runs the app in raw mode (Ctrl+C arrives as
0x03 and is handled as CANCELED, Ctrl+Q quits), and guarantees the terminal is
restored (echo, cursor, mouse protocol disabled) even after Ctrl+C, SIGTERM or
SIGHUP.
"""

from __future__ import annotations

import os
import signal
import sys

# ESC delay must be set before curses.initscr reads the environment.
os.environ.setdefault("ESCDELAY", "25")

import curses

from .app import App
from .keys import MOUSE_DISABLE, MOUSE_ENABLE
from .screen import TerminalTooSmall


def _restore_terminal():
    try:
        sys.stdout.write(MOUSE_DISABLE)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        curses.echo()
    except curses.error:
        pass


def _install_signal_handlers():
    def _handler(signum, frame):
        raise SystemExit(0)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    filepath = None
    for arg in argv:
        if arg in ("-h", "--help"):
            print("usage: python3 -m descartes [file.vg1|file.wk1|file.dif|file.txt]")
            return 0
        if not arg.startswith("-"):
            filepath = arg
    try:
        sys.stdout.write(MOUSE_ENABLE)
        sys.stdout.flush()
    except Exception:
        pass
    _install_signal_handlers()

    def _run(stdscr):
        curses.raw()          # no ISIG: Ctrl+C (0x03) is handled as CANCELED in-app
        curses.noecho()
        app = App(stdscr, filepath)
        app.run()

    try:
        curses.wrapper(_run)
    except TerminalTooSmall as e:
        print(str(e))
        return 2
    except KeyboardInterrupt:
        pass
    finally:
        _restore_terminal()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
