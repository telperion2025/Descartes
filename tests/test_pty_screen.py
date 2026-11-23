"""PTY integration test under TERM=screen-256color (the tmux/screen pane TERM):
the app must run, parse XTerm SGR mouse reports delivered by the outer terminal,
and never leak mouse bytes into cell entry. Spec section 6.3 / acceptance item 8.

Skipped where pty is unavailable (Windows).
"""

import os
import select
import time

import pytest

pty = pytest.importorskip("pty")
import fcntl
import struct
import termios

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spawn(term="screen-256color", rows=25, cols=80):
    pid, fd = pty.fork()
    if pid == 0:
        # set the window size in the child before exec (avoids the race where
        # curses reads the default 80x24 before the parent's ioctl lands);
        # LINES/COLUMNS are honoured by ncurses' use_env() before the ioctl
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.environ["TERM"] = term
        os.environ["LINES"] = str(rows)
        os.environ["COLUMNS"] = str(cols)
        os.chdir(REPO)
        os.execvp("python3", ["python3", "-m", "descartes"])
    return pid, fd


def _drain(fd, t=0.5):
    out = b""
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                out += os.read(fd, 65536)
            except OSError:
                break
    return out



def _wait_for(fd, needle, timeout=8.0):
    """Drain until `needle` appears in the output (or timeout); returns bytes."""
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        out += _drain(fd, 0.25)
        if needle in out:
            break
    return out

def _send(fd, data, wait=0.3):
    os.write(fd, data)
    time.sleep(wait)


@pytest.fixture
def app_proc():
    pid, fd = _spawn()
    # wait until the app is up and rendering (raw mode, READY on screen)
    deadline = time.time() + 15
    startup = b""
    while time.time() < deadline:
        startup += _drain(fd, 0.3)
        if b"READY" in startup:
            break
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        except ChildProcessError:
            break
    assert b"READY" in startup, "app did not start under pty"
    yield pid, fd
    try:
        os.write(fd, b"\x11")  # Ctrl+Q
        time.sleep(0.4)
        _drain(fd, 0.5)
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def test_app_runs_under_screen_term(app_proc):
    pid, fd = app_proc
    _send(fd, b"123\r", 0.2)
    out = _wait_for(fd, b"123")
    assert b"123" in out


def test_mouse_click_menu_bar_opens_menu(app_proc):
    pid, fd = app_proc
    # SGR 1006 click on the menu bar (app row 0 -> SGR y=1; WORKSHEET at x=1..10 -> x=3)
    _send(fd, b"\x1b[<0;3;1M", 0.2)
    _send(fd, b"\x1b[<0;3;1m", 0.2)   # release
    out = _wait_for(fd, b"WORKSHEET")
    assert b"WORKSHEET" in out and b"RANGE" in out   # dropdown tree opened by mouse


def test_mouse_click_cell_then_type_lands_in_cell_no_leak(app_proc):
    pid, fd = app_proc
    # click grid cell: x=8 (col A), y=6 (sheet row 2 -> A3): SGR is 1-based
    _send(fd, b"\x1b[<0;9;7M", 0.2)
    _send(fd, b"\x1b[<0;9;7m", 0.2)
    _send(fd, b"77\r", 0.2)
    out = _wait_for(fd, b"77")
    assert b"77" in out
    # mouse bytes must never leak into cell entry as literal text
    assert b"[<0;" not in out


def test_esc_closes_menu_under_screen_term(app_proc):
    pid, fd = app_proc
    _send(fd, b"/", 0.2)
    out = _wait_for(fd, b"WORKSHEET")
    assert b"WORKSHEET" in out          # menu open
    _send(fd, b"\x1b", 0.2)            # lone ESC closes it
    time.sleep(0.6)
    _send(fd, b"9\r", 0.2)             # entry after ESC must be clean
    out = _wait_for(fd, b"9")
    assert b"9" in out
    tail = out[-400:]
    assert b"[<" not in tail
