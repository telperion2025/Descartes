# Descartes 1-2-3

A Unix (BSD/macOS) and Linux terminal clone of **Lotus 1-2-3 Release 3.1**, written in
Python 3 with the standard-library `curses` module only — no pip dependencies, no GUI
toolkit. It reproduces the faithful VGA/DOS text look: the cyan/blue control panel, slash
pulldown menus, the two-mode (READY / VALUE / EDIT / MENU / POINT) state machine, `@`
functions, range operations, text-mode graphs, a database (`/DATA`), and mouse support.

## Requirements

- Python >= 3.9 (stdlib only)
- A terminal of at least **80x25** (wider terminals extend the grid area)
- Linux (ncurses) or macOS/BSD; works over ssh and inside tmux/screen
- UTF-8 preferred but not required (graph glyphs fall back to ASCII)

## Running

```sh
python3 -m descartes                 # empty worksheet
python3 -m descartes book.vg1        # load a native file
python3 -m descartes data.wk1        # import Lotus WK1/WKS (first sheet)
python3 -m descartes table.dif       # import DIF
python3 -m descartes notes.txt       # import delimited text
```

On startup the XTerm mouse protocol (SGR 1006 with 1000 fallback) is enabled; on exit —
even after Ctrl+C — the terminal is restored (echo, cursor, mouse protocol disabled).
Set `ESCDELAY=25` (done automatically) for snappy Alt-menu use.

## Quick start

| Key | Action |
| --- | --- |
| type `123` Enter | enter a number (cursor moves down) |
| type `@SUM(A1..A2)` Enter | enter a formula |
| `F2` | edit the current cell |
| `/` or `F10` | open the menu tree |
| `F9` | recalculate now |
| arrows / PgUp / PgDn / Home / End | move the cell cursor |
| `~` | jump to A1 |
| Esc | cancel entry / command |
| Ctrl+C | CANCELED — unwind to READY |
| mouse click / double-click / wheel | move cursor / edit cell / scroll |

Full documentation:

- [User manual](docs/user-manual.md) — day-to-day use, menus, graphs, data commands, mouse
- [Reference manual](docs/reference-manual.md) — formula language, `@`functions, format
  codes, full slash-command tree, file formats (.vg1 / WK1 / DIF), keyboard map, screen
  coordinates, module architecture

## Architecture

```
descartes/
  __main__.py   entry: python -m descartes [file]
  app.py        main loop, mode state machine, command dispatch
  screen.py     all curses drawing (only module besides keys.py that imports curses)
  keys.py       key decoding incl. terminfo quirks, XTerm mouse protocol parser
  grid.py       Worksheet, Cell, Range model, addressing
  engine.py     formula parser, dependency graph, recalculation
  functions.py  @function registry
  menus.py      slash-menu tree data + menu state machine
  graph.py      text-mode graph renderer
  data_cmd.py   /DATA query, sort, table, frequency, regression
  persist.py    .vg1 (JSON) native format + WK1 import/export + DIF/TEXT import
  fmt.py        number/date display formatting codes
```

Only `screen.py` and `keys.py` import curses; everything else is pure logic and is tested
without a terminal.

## Testing

```sh
python3 -m pip install pytest     # test-only dependency
python3 -m pytest                 # 122 tests: logic + fake-curses UI + golden snapshots
```

The suite covers the spec's acceptance walkthrough end-to-end (entry/`@SUM`, `/COPY`
relative vs `$` refs, save/reload, `/DATA FILL` + `/SORT`, `/GRAPH` bar view, mouse
behaviors) plus a 20k-cell recalculation benchmark (spec target < 300 ms; measured
~25 ms).

## Non-goals (per spec)

Macros (`{commands}`), multi-sheet printing and 3-D ranges, backsolver, mouse
column-resize handles, color beyond text modes.
