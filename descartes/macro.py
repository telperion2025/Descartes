"""1-2-3 style macros (Release 3.1 `{...}` command language), pragmatic subset.

A macro lives in a label cell whose raw text starts with `{`. It runs when its
cell is the target of a single-letter range name and the user presses Alt+letter
(1-2-3 assigns macros to Alt+letter keys this way).

Tokens: `{COMMAND args}` for commands, `{KEYNAME}` for special keys, and bare
text between commands is sent to the app as keystrokes (so macros drive the real
UI: typing `12{ENTER}` enters 12 into the current cell).

Commands: GOTO, LET/PUT, BEEP, RECALC, WAIT, SYSTEM, BRANCH, CALL, RETURN,
HALT, QUIT, IF / ELSE / END, LOOK (no-op), INPUT (needs /RANGE INPUT),
GETNUMBER / GETLABEL (prompt + store into a cell), MENUBRANCH (feed a cell's
text as menu keystrokes), DEFINE (create a range name, 3-D aware). Unknown
commands halt with an error.
"""

from __future__ import annotations

import time

from .engine import Ctx, compile_node, parse
from .grid import is_valid_name, parse_address, split_range_any
from .keys import KeyEvent

KEY_NAMES = {"LEFT", "RIGHT", "UP", "DOWN", "HOME", "ENDKEY", "PGUP", "PGDN",
             "INS", "DEL", "BS", "BACKSPACE", "TAB", "ENTER", "ESC", "F1", "F2",
             "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"}
KEY_ALIASES = {"ENDKEY": "END"}

COMMANDS = {"GOTO", "LET", "PUT", "BEEP", "RECALC", "WAIT", "SYSTEM", "BRANCH",
            "CALL", "RETURN", "HALT", "QUIT", "IF", "ELSE", "END", "LOOK",
            "INPUT", "GETNUMBER", "GETLABEL", "MENUBRANCH", "DEFINE"}


class MacroError(Exception):
    pass


def tokenize_macro(text: str):
    """-> list of ('keys', str) / ('key', NAME) / ('cmd', NAME, args_str)."""
    toks = []
    buf = []
    i, n = 0, len(text)

    def flush():
        if buf:
            toks.append(("keys", "".join(buf)))
            buf.clear()

    while i < n:
        if text[i] == "{":
            j = text.find("}", i + 1)
            if j < 0:
                raise MacroError("unterminated { in macro")
            flush()
            inner = text[i + 1:j].strip()
            head = inner.split(None, 1)
            name = (head[0] if head else "").upper()
            args = head[1] if len(head) > 1 else ""
            if name in COMMANDS:
                toks.append(("cmd", name, args))
            elif name in KEY_NAMES:
                toks.append(("key", KEY_ALIASES.get(name, name)))
            elif not name:
                pass
            else:
                raise MacroError(f"unknown macro command {{{name}}}")
            i = j + 1
        else:
            buf.append(text[i])
            i += 1
    flush()
    return toks


def split_args(args: str):
    """Split on top-level commas, respecting double-quoted strings and parens."""
    out, cur, inq, depth = [], [], False, 0
    for ch in args:
        if ch == '"':
            inq = not inq
            cur.append(ch)
        elif ch == "(" and not inq:
            depth += 1
            cur.append(ch)
        elif ch == ")" and not inq:
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and not inq and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur or args.strip():
        out.append("".join(cur).strip())
    return out


def _find_matching(toks, start):
    """Index of the END matching the IF at `start` (nesting-aware), plus the
    ELSE index if present."""
    depth = 0
    else_idx = None
    for i in range(start, len(toks)):
        t = toks[i]
        if t[0] == "cmd" and t[1] == "IF":
            depth += 1
        elif t[0] == "cmd" and t[1] == "END":
            depth -= 1
            if depth == 0:
                return i, else_idx
        elif t[0] == "cmd" and t[1] == "ELSE" and depth == 1 and else_idx is None:
            else_idx = i
    raise MacroError("IF without END")


def _find_end(toks, start):
    """Index of the END closing the block opened before `start`."""
    depth = 0
    for i in range(start, len(toks)):
        t = toks[i]
        if t[0] == "cmd" and t[1] == "IF":
            depth += 1
        elif t[0] == "cmd" and t[1] == "END":
            if depth == 0:
                return i
            depth -= 1
    raise MacroError("ELSE without END")


class MacroRunner:
    def __init__(self, app, max_steps=10000, max_depth=16):
        self.app = app
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.steps = 0
        self.stack = []
        self.suspended = None

    def resume(self):
        """Continue a macro suspended by {INPUT}/{GETNUMBER}/{GETLABEL}."""
        if not self.suspended:
            return
        toks, pc, fkey = self.suspended
        self.suspended = None
        try:
            self._exec(toks, pc, fkey)
        except RecursionError:
            raise MacroError("macro recursion too deep")

    def _tick(self):
        self.steps += 1
        if self.steps > self.max_steps:
            raise MacroError("macro step limit exceeded (runaway macro?)")

    def run_cell(self, key):
        raw = self.app.sheet.raw_at(*key)
        if raw is None or not raw.startswith("{"):
            raise MacroError(f"{key} is not a macro cell")
        self.stack = []
        self.suspended = None
        try:
            self._exec(tokenize_macro(raw), 0, key)
        except RecursionError:
            raise MacroError("macro recursion too deep")

    def _exec(self, toks, start, fkey):
        pc = start
        while pc < len(toks):
            if not getattr(self.app, "running", True) or self.suspended:
                break
            self._tick()
            t = toks[pc]
            if t[0] == "keys":
                for ch in t[1]:
                    self.app.handle(KeyEvent("char", ch=ch))
                pc += 1
            elif t[0] == "key":
                name = t[1]
                if name in ("BS", "BACKSPACE"):
                    name = "BACKSPACE"
                self.app.handle(KeyEvent("key", name=name))
                pc += 1
            elif t[0] == "cmd" and t[1] == "BRANCH":
                c, r = parse_address(t[2])
                raw = self.app.sheet.raw_at(c, r)
                if raw is None or not raw.startswith("{"):
                    raise MacroError(f"BRANCH target {t[2]} is not a macro")
                toks = tokenize_macro(raw)
                fkey = (c, r)
                pc = 0
            elif t[0] == "cmd":
                _, name, args = t
                pc = self._cmd(toks, pc, name, args, fkey)
            else:
                pc += 1

    def _eval_cond(self, expr, fkey):
        from .functions import Error
        try:
            v = compile_node(parse(expr))(Ctx(self.app.engine, fkey))
        except Exception as e:
            raise MacroError(f"bad IF expression {expr!r}: {e}")
        from .engine import RangeValue
        if isinstance(v, RangeValue):
            v = v.scalar(Ctx(self.app.engine, fkey))
        if isinstance(v, Error):
            raise MacroError(f"IF expression errored: {expr!r}")
        if isinstance(v, str):
            return v != ""
        return bool(v)

    def _cmd(self, toks, pc, name, args, fkey):
        app = self.app
        if name == "GOTO":
            c, r = parse_address(args)
            app.move(c, r)
            return pc + 1
        if name in ("LET", "PUT"):
            a = split_args(args)
            if len(a) != 2:
                raise MacroError(f"{{{name}}} needs cell, value")
            c, r = parse_address(a[0])
            app.sheet.set_entry(c, r, a[1])
            app.engine.note_edit((c, r))
            app._touch(app.active, (c, r))
            app._recalc()
            return pc + 1
        if name == "BEEP":
            try:
                app.screen.stdscr.beep()
            except Exception:
                pass
            return pc + 1
        if name == "RECALC":
            app._recalc(force=True)
            return pc + 1
        if name == "WAIT":
            try:
                time.sleep(min(5.0, float(args or "0")))
            except ValueError:
                pass
            return pc + 1
        if name == "SYSTEM":
            app.cmd_system(args)
            return pc + 1
        if name == "LOOK":
            return pc + 1
        if name == "INPUT":
            rng = getattr(app, "input_range", None)
            if rng is None:
                raise MacroError("{INPUT} requires /RANGE INPUT first")
            app.move(*rng[0])
            app.begin_macro_wait("input", "Enter data:", None, self)
            self.suspended = (toks, pc + 1, fkey)
            return len(toks)
        if name in ("GETNUMBER", "GETLABEL"):
            a = split_args(args)
            if len(a) != 2:
                raise MacroError(f"{{{name}}} needs \"prompt\", cell")
            prompt = a[0].strip()
            if len(prompt) >= 2 and prompt.startswith('"') and prompt.endswith('"'):
                prompt = prompt[1:-1]
            c, r = parse_address(a[1])
            app.begin_macro_wait("number" if name == "GETNUMBER" else "text",
                                 prompt, (c, r), self)
            self.suspended = (toks, pc + 1, fkey)
            return len(toks)
        if name == "MENUBRANCH":
            c, r = parse_address(args)
            raw = app.sheet.raw_at(c, r)
            if raw is None:
                raise MacroError(f"MENUBRANCH target {args} is empty")
            text = raw[1:] if raw.startswith("'") else raw
            for ch in text:
                app.handle(KeyEvent("char", ch=ch))
            return pc + 1
        if name == "DEFINE":
            if ":" not in args:
                raise MacroError("{DEFINE} needs name:range")
            nm, rngtxt = args.split(":", 1)
            nm = nm.strip().upper()
            if not is_valid_name(nm):
                raise MacroError(f"bad range name {nm!r}")
            sheets, c1, c2 = split_range_any(rngtxt)
            entry = {"sheet": sheets[0] if sheets else app.active, "range": (c1, c2)}
            if sheets and sheets[0] != sheets[1]:
                entry["sheets"] = [sheets[0], sheets[1]]
            app.names[nm] = entry
            app._rebuild_xdeps()
            return pc + 1
        if name == "CALL":
            if len(self.stack) >= self.max_depth:
                raise MacroError("macro CALL depth limit")
            c, r = parse_address(args)
            raw = app.sheet.raw_at(c, r)
            if raw is None or not raw.startswith("{"):
                raise MacroError(f"CALL target {args} is not a macro")
            self.stack.append((toks, pc + 1, fkey))
            self._exec(tokenize_macro(raw), 0, (c, r))
            return len(toks)
        if name == "RETURN":
            if self.stack:
                toks2, pc2, fkey2 = self.stack.pop()
                self._exec(toks2, pc2, fkey2)
            return len(toks)
        if name in ("HALT", "QUIT"):
            if name == "QUIT":
                app.quit()
            return len(toks)
        if name == "IF":
            end_idx, else_idx = _find_matching(toks, pc)
            cond = self._eval_cond(args, fkey)
            if cond:
                return pc + 1
            return (else_idx + 1) if else_idx is not None else end_idx + 1
        if name == "ELSE":
            return _find_end(toks, pc + 1) + 1
        if name == "END":
            return pc + 1
        raise MacroError(f"unknown macro command {{{name}}}")
