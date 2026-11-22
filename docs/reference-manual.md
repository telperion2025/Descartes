# Descartes 1-2-3 — Reference Manual

Technical reference for the Descartes 1-2-3 terminal clone: architecture, the worksheet
and cell model, the formula language and `@`function registry, format codes, the slash
command tree, file formats, keyboard/mouse mapping, screen coordinates, and platform
notes. For day-to-day usage see the [user manual](user-manual.md).

## 1. Package layout and module responsibilities

```
descartes/
  __init__.py     version
  __main__.py     entry point: python3 -m descartes [file]; terminal setup/teardown
  app.py          main loop, mode state machine (READY/VALUE/LABEL/EDIT/POINT/MENU/
                  DIALOG/ERROR + GRAPH/SYSTEM/HELP overlays), command dispatch
  screen.py       all curses drawing; fixed 80x25 layout; color pairs (§6)
  keys.py         key decoding (terminfo quirks, raw ESC fallbacks) + XTerm mouse parser
  grid.py         Worksheet / Cell / Range model, addressing, entry classification
  engine.py       tokenizer, recursive-descent parser, closure compiler, dependency
                  graph, recalculation, copy-shift
  functions.py    @function registry and the value model (Error ERR/NA)
  menus.py        slash-menu tree data + pure menu navigation state machine
  graph.py        text-mode graph renderer (pure logic)
  data_cmd.py     /DATA FILL, SORT, QUERY, TABLE, FREQUENCY, REGRESSION (pure logic)
  persist.py      .vg1 native JSON, WK1 import/export, DIF, TEXT/NUMBERS import
  fmt.py          number/date display formatting codes + Lotus date serials + WK1 fmt byte
```

**Portability rule:** only `screen.py` and `keys.py` import `curses`. Everything else is
pure logic and is unit-tested without a terminal (fake-curses harness drives the UI).

## 2. Worksheet and cell model

- Grid limits (Release 3.1): **256 columns A..IV x 8192 rows**. Cell address =
  letters + 1-based row (`A1..IV8192`). Internally cells are keyed
  `(sheet_name, col, row)` with zero-based `col`/`row`, so later phases can add
  multi-sheet and 3-D ranges without migration; a file currently holds a single sheet.
- Column widths: per-column 0-240 chars, global default **9**; width affects display
  only, never storage. Row height is one screen line.
- `Cell`:

  | field | meaning |
  | --- | --- |
  | `raw` | exactly what the user typed (`None` = empty) |
  | `kind` | `EMPTY` \| `LABEL` \| `NUMBER` \| `FORMULA` |
  | `value` | computed value (float / str / Error; `None` = blank) |
  | `fmt` | format code dict or `None` (§5) |
  | `protected` | protection flag |
  | `hidden` | formula hidden (shows `*****`) |
  | `align` | label alignment override (`left`/`center`/`right`) |

- Entry classification: empty → EMPTY; first char in `' ^ "` → LABEL with that
  alignment; first char in `0-9 + - . ( @ # $` → NUMBER if it parses as a number
  (a trailing `%` divides by 100), else FORMULA; anything else → LABEL (left).
- Special values: `ERR` (error: 0-div, bad argument, circular reference), `NA` (`@NA`).
  Blanks are 0 in arithmetic and `""` in string operations. String equality is
  case-insensitive; in comparisons numbers sort before text.

## 3. Recalculation architecture

- Each FORMULA cell is parsed once into an AST and compiled to a closure; referenced
  single cells and ranges are collected into a dependency graph.
- On any edit the engine marks the edited cell dirty and propagates dirtiness to all
  transitive dependents; recalculation evaluates dirty formulas in topological order.
- Cycles: participants of a circular reference become `ERR`; with
  `/WORKSHEET GLOBAL RECALCULATION ITERATION n` (`n > 0`) they are instead evaluated
  `n` times (iterative calculation), starting from blank.
- Modes: AUTO (recalc after every edit) and MANUAL (only F9 / `recalc(force=True)`
  recalculates; the **CALC** indicator lights while dirty).
- Performance: full recalculation of a 20,000-formula-cell grid is measured by the test
  suite at ~25 ms (spec target < 300 ms) — see `tests/test_engine.py::test_performance_20k`.

## 4. Formula language

### 4.1 Syntax

- Literals: numbers (`123`, `-4.5`, `.5`, `1e3`), strings (`"text"`), percent postfix
  (`50%` = 0.5).
- Cell refs `A1`, absolute `$A$1` / `$A1` / `A$1`; ranges `A1..B2` (corners normalize).
- Operators, precedence high → low:

  ```
  unary + -  and #NOT#
  ^
  * /
  + -
  =  <>  <  >  <=  >=
  #AND#  #OR#
  &        (string concatenation, lowest)
  ```

- Parentheses; function calls `@NAME(arg, ...)` (also `@NA` with no parentheses).
- A leading `+` is the formula indicator, not an operator.
- A range used in a scalar context resolves by implicit intersection with the formula
  cell (single-row/single-column ranges intersect by column/row); otherwise the first
  cell of the range is used.

### 4.2 @functions

Numeric/statistical: `@SUM @AVG @COUNT @MAX @MIN @STD @VAR @SQRT @ROUND @INT @ABS @EXP
@LN @LOG(x[,base]) @PI @SIN @COS @TAN @ASIN @ACOS @ATAN @MOD @RAND`

Logic/lookup: `@IF @HLOOKUP @VLOOKUP @INDEX @CHOOSE @ISERR @ISNA @ISNUMBER @ISSTRING @ERR
@NA`

String/date: `@LENGTH @STRING @VALUE @LEFT @RIGHT @MID @UPPER @LOWER @TRIM @REPEAT @CHAR
@CODE @DATE @DATEVALUE @NOW @TIME @YEAR @MONTH @DAY @TODAY` (plus `@HOUR @MINUTE @SECOND
@COLS @ROWS`)

Financial: `@FV @PV @NPV @IRR @TERM @RATE @PAYMENT`

Semantics notes: `@STD`/`@VAR` are sample statistics (n-1); `@HLOOKUP`/`@VLOOKUP` use
1-2-3 approximate matching on a sorted key row/column (exact match wins); ranges inside
statistical functions ignore text/blank cells; `@DATE(y,m,d)` maps years < 100 to
19yy; date serials use the Lotus epoch (serial 1 = 1900-01-01, with the historical
fictitious 1900-02-29 at serial 60, so 1900-03-01 = 61).

The 1-2-3 **sort order** for `/DATA SORT` and `/FIND` is distinct from general text
comparison: numbers first, then text as digits `0-9` < `A-Z` < `a-z` (implemented as
`data_cmd.lotus_sort_key`).

## 5. Format codes

A format code is a JSON-friendly dict. `fmt.display(value, fmt)` renders a cell.

| type | keys | example output |
| --- | --- | --- |
| `None` (general) | — | `579`, `0.3` |
| `fixed` | `decimals` | `3.14` |
| `sci` | `decimals` | `1.23E+05` |
| `currency` | `decimals` | `$1,234.50` |
| `percent` | `decimals` | `12.3%` |
| `comma` | `decimals` | `1,234,567.89` |
| `date` | `style`: `dd-mmm-yy`, `dd-mmm`, `mmm-yy`, `dd/mm/yy`, `mm/dd/yy`, `dd/mm/yyyy`, `dd-mmm-yyyy` | `05-Jan-92` |
| `time` | `style`: `hh:mm:ss`, `hh:mm` | `12:00:00` |
| `text` | — | numbers as text |
| `hidden` | — | cell displays blank |

`/RANGE FORMAT` offers FIXED, SCIENTIFIC, CURRENCY, PERCENT, COMMA, GENERAL, TEXT, DATE,
TIME, HIDDEN.

## 6. Screen layout and colors (screen coordinates)

Fixed 80-column layout, absolute coordinates (0-based rows in code, 1-based as printed):

| row (1-based) | content |
| --- | --- |
| 1 | menu bar (blank blue bar in READY; menu tree in MENU) |
| 2 | control panel: `A1  READY` left, prompt right |
| 3 | cell content line (raw text of highlighted cell) |
| 4 | column headers (current column highlighted); gutter = 7 chars |
| 5-22 | worksheet rows |
| 23 | indicators CAPS NUM SCROLL CALC MEM OVR/INS + clock |
| 24 | message line |
| 25 | error line |

Color pairs (defined once in `screen.py`), matching the real Lotus 1-2-3 3.x DOS
look: black worksheet, blue menu bar, cyan control panel, reverse-video cursor cell.
Mono terminals use `A_REVERSE` for the menu/panel/highlight/cursor pairs.

| pair | fg/bg | used for |
| --- | --- | --- |
| 1 | white/black | worksheet background, column headers, row gutter |
| 2 | white/blue | menu bar (blank blue bar in READY) and dropdown boxes |
| 3 | black/white + bold | highlighted menu item, dialog cursor, POINT range |
| 4 | white/black | column headers, row gutter |
| 5 | white/black + bold | current column header / row, frozen title cols |
| 6 | red/white | error messages |
| 7 | black/white + bold | cursor cell (reverse video, as in 1-2-3) |
| 8 | black/cyan | control panel (rows 2-3) and indicator line (row 23) |

## 7. Slash command reference

Top level: `WORKSHEET RANGE COPY MOVE FILE PRINT GRAPH DATA SYSTEM ADD-IN`
(full tree data in `menus.py::MENU_TREE`).

```
/WORKSHEET
  GLOBAL  LABEL-PREFIX | COL-PREFIX | ZERO-SUPPRESS | FORMAT
          RECALCULATION  AUTO | MANUAL | ITERATION (prompt: count)
          PROTECTION  ENABLE | DISABLE
  INSERT  COLUMN | ROW          (prompt: cell)
  DELETE  COLUMN | ROW          (prompt: cell)
  ERASE                         (prompt: range)
  COLUMN-WIDTH  SET (width, column) | RESET (column)
  HIDE | UNHIDE                 (prompt: column cell)
  WINDOW  HORIZONTAL | VERTICAL | CLEAR | SYNC | UNSYNC | CHANGE
  TITLES  BOTH | HORIZONTAL | VERTICAL (prompt: range) | CLEAR
  STATUS | PAGE
/RANGE
  FORMAT (range, then choice FIXED/SCIENTIFIC/CURRENCY/PERCENT/COMMA/GENERAL/TEXT/DATE/TIME/HIDDEN)
  ERASE (range) | JUSTIFY (range) | LABEL (range, LEFT/CENTER/RIGHT)
  NAME  CREATE (name, range) | DELETE (name) | LABELS (range, LEFT/RIGHT/TOP/BOTTOM) | RESET | TABLE
  PROTECT | UNPROTECT (range) | INPUT (range)
  TRANSPOSE (range, dest cell) | VALUE (range, dest cell) | SEARCH (range, text)
/COPY | /MOVE                  (source range, dest anchor/range; tiling on multiples)
/FILE
  SAVE | SAVE-AS (file) | LOAD (file)
  COMBINE  COPY | ADD | SUBTRACT  (file, range-in-file, dest cell)
  XTRACT (file, range, WK1|DIF|TEXT) | ERASE (file, YES/NO) | LIST (pattern)
  IMPORT  DIF | TEXT | NUMBERS (file, anchor cell)
  DIRECTORY (path) | QUIT (exit the program)
/PRINT
  PRINTER (device or |command, range) | FILE (file, range) | RANGE (range, PRINTER|FILE)
  LINES-PAGE (n) | MARGINS (l r t b) | BORDERS (YES/NO) | HEADER (text)
/GRAPH
  TYPE  LINE | BAR | XY | STACKED-BAR | PIE
  X-RANGE (range) | A..F (range, name) | RESET | VIEW | SAVE (name)
  OPTIONS  LEGEND (text) | TITLES (text) | GRID | SCALE
/DATA
  FILL (range, start, step, stop)
  TABLE  1-VARIABLE (range, input cell) | 2-VARIABLE (range, row cell, col cell)
  SORT  DATA-RANGE (range) | PRIMARY-KEY (cell, YES/NO) | SECONDARY-KEY (cell, YES/NO)
        | RESET | GO
  QUERY  INPUT (range) | CRITERION (range) | OUTPUT (range) | UNIQUE
         | FIND | DELETE | EXTRACT
  FREQUENCY (data range, bin range)
  REGRESSION (x range, y range, output cell)
/SYSTEM (shell command — output in a scroll buffer, any key returns)
/ADD-IN  -> "No add-ins loaded"
```

**Prompt chain paradigm:** prompt text on row 2, entry reverse-highlighted; Enter accepts,
Esc cancels the whole chain, Backspace re-enters the previous prompt. Range/cell prompts
accept typed text or POINT-mode selection (Enter on an empty entry switches to POINT;
`.` anchors/extends; Enter submits; a mouse click submits).

## 8. Keyboard map

| key | READY | entry modes (VALUE/LABEL/EDIT) | POINT | MENU | DIALOG |
| --- | --- | --- | --- | --- | --- |
| printable | start VALUE (if `0-9+-.(@$#`) else LABEL | append at cursor | `.` anchors range | letter = quick select; `/` closes | append |
| Enter | move down | commit | submit range/cell | select item | accept prompt / enter POINT if range prompt empty |
| Esc | clear message | abort | cancel | back one level | cancel chain |
| Backspace | erase cell | delete char | — | back one level | delete char / previous prompt |
| arrows | move | move entry cursor (L/R) | move | navigate | move entry cursor |
| PgUp/PgDn/Home/End | scroll/jump | cursor to start/end | move | — | — |
| F1 | help screen | | | | |
| F2 / double-click | EDIT | | | | |
| F9 | force recalc | | | | |
| F10 | open menu | | | close menu | |
| `~` | jump to A1 | | | | |
| Ctrl+C | CANCELED, unwind to READY | | | | |
| Ctrl+Q / Alt+Q | quit (terminal restored even after Ctrl+C) | | | | |

Key decoding (`keys.py`): curses key codes first; raw `\x1bOP..\x1bOS` (F1-F4) and CSI
sequences as fallback when terminfo lacks them; both `KEY_BACKSPACE` and DEL (0x7f) decode
as backspace (macOS sends DEL); `ESCDELAY=25` ms is set before `initscr` on both Linux and
macOS/BSD for snappy Alt-keys.

## 9. Mouse protocol

- Enabled at startup by writing `ESC[?1000h ESC[?1006h`; disabled in a `finally` and on
  SIGTERM/SIGHUP with `ESC[?1006l ESC[?1000l`.
- Parsed manually in `keys.py::MouseParser` (SGR 1006 `ESC[<Cb;Cx;Cy M/m`, legacy 1000
  `ESC[M Cb Cx Cy`), normalized to `MouseEvent(x, y, button, press, release, click_count
  (<=400 ms double/triple), drag, wheel_up, wheel_down, modifiers)`.
- Behavior table in [user manual §10](user-manual.md#10-mouse).
- tmux >= 3.2 with `set -g mouse on` passes 1006 through (origin = pane); TERM=screen*
  without mouse support is detected and the app stays keyboard-only; malformed mouse
  bytes are discarded and never leak into cell entry.

## 10. File formats

### 10.1 .vg1 (native, JSON)

```json
{
  "format": "descartes-vg1", "version": 1,
  "sheets": {"A": {"cells": [{"c":0,"r":0,"raw":"123","kind":"NUMBER","value":123.0,
                             "fmt":null,"protected":false,"hidden":false,"align":null}],
                  "columns": {"0": 12}, "global_format": null, "protection": false,
                  "label_prefix": "'", "zero_suppress": false}},
  "names": {"TOTAL": {"sheet": "A", "range": [[0,0],[2,0]]}},
  "recalc": {"mode": "auto", "iterations": 0},
  "cursor": [0, 0], "graphs": [...], "print": {...}, "window": {...}
}
```

Cells are ordered deterministically by (sheet, row, col) for clean diffs.

### 10.2 WK1 (Lotus 1-2-3 Release 2.x binary) — import/export

Record stream: `{u16 opcode LE, u16 length LE, payload[length]}`, terminated by EOF
(0x0001). BOF (0x0000) carries the version word (writer emits 0x0406; readers accept
0x0404/0x0405/0x0406 and the WK3-style 4-byte `0x0005 0x0806`). Cell records are decoded
for the WK1/WKS opcode family below (payload = fmt byte, col u16, row u16, ...); WK3/WK4
files are accepted and their unknown records skipped record-by-record, which decodes the
first sheet's cell records when they use WK1-family opcodes — other records are ignored
gracefully (documented limitation). Cell records:

| opcode | record | payload after col/row |
| --- | --- | --- |
| 0x000D | INTEGER | i16 value |
| 0x000E | NUMBER | f64 LE value |
| 0x000F | LABEL | prefix char (`' ^ "`), NUL-terminated CP437 string |
| 0x0010 | FORMULA | f64 cached result, u16 token length, RPN tokens |
| 0x0008 | COLW1 | u8 width (best effort) |
| 0x0002 | CALCMODE | u8: 0 auto, 1 manual |

Formula tokens (RPN): 0x00 f64 const, 0x01 cell ref (col,row as u16; 0x8000 flag = 16-bit
two's-complement offset from the formula cell), 0x02 range (4x u16), 0x03 end, 0x04 unary
+, 0x05 i16 const, 0x06 NUL-terminated string, 0x08-0x17 operators (`- + - * / ^ = <> <= >=
< > #AND# #OR# #NOT# &`), 0x1F @NA, 0x20 @ERR, 0x21+ functions (0x50-0x54, 0x68-0x69 take an
argument-count byte). Import translates the common subset to infix text; anything
unsupported keeps the cached numeric result. Export encodes labels/numbers and formulas
(the common token subset; otherwise the cached value is written as a NUMBER).

The WK1 format byte: bit 7 protected, bits 4-6 type (0 fixed, 1 sci, 2 currency, 3 percent,
4 comma, 7 special), bits 0-3 decimals or special subtype (2 dd/mm/yy, 3 dd/mm, 4 mm/yy,
5 text, 6 hidden, 7-10 dates, 11-12 times); 0xFF = default. See `fmt.wk1_byte_to_fmt`.

### 10.3 DIF / TEXT / NUMBERS

- DIF: `TABLE/VECTORS/TUPLES/DATA` headers, `V,<num>` / `S,"str"` entries, `-1,0` row ends,
  `EOD`. Import and export (`/FILE XTRACT ... DIF`).
- TEXT: comma-delimited, `"quoted"` strings with `""` escapes; NUMBERS mode keeps only
  numeric fields. Numbers/plain text only — no formula reconstruction.

## 11. Platform notes

| behavior | Linux/glibc | macOS/BSD |
| --- | --- | --- |
| curses | built-in | built-in |
| ESC delay | `ESCDELAY=25` set before `initscr` | same env var, read at `initscr` |
| Backspace | `KEY_BACKSPACE` | may send DEL 0x7f — both decoded |
| F1..F12 | terminfo | `TERM=xterm-256color` fine; raw `\x1bOP..` fallback |
| colors | 8/256 via `curses.COLORS` feature-detect | iTerm2/Terminal.app also 256 |

Must work over ssh and inside tmux/screen. UTF-8 preferred but not required: cell
rendering clips to the display width and graph bars fall back to `#`.

## 12. Testing

`python3 -m pytest` — pure-logic tests (no curses import outside screen/keys) plus a
terminal harness with a fake curses screen: drawing commands snapshot to text files
under `tests/golden/` and golden tests compare (regenerate with `REGEN_GOLDEN=1`).
The acceptance walkthrough of the spec is driven end-to-end in
`tests/test_app_walkthrough.py`.

## 13. Non-goals

Macros (3.1 `{commands}`), multi-sheet printing and 3-D ranges, backsolver, mouse
column-resize handles, color beyond text modes, multi-line cells.
