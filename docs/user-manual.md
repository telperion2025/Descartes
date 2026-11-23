# Descartes 1-2-3 — User Manual

Descartes 1-2-3 is a terminal clone of Lotus 1-2-3 Release 3.1. This manual describes how
to use it day to day: the screen, the modes, entering data and formulas, the slash menus,
files, printing, graphs, database commands, and the mouse.

Run it with:

```sh
python3 -m descartes [file.vg1|file.wk1|file.dif|file.txt]
```

## 1. The screen (80x25 DOS text layout)

```
 row 1   menu bar (blank blue in READY; the menu tree after / or F10)
 row 2   control panel: cell address + mode on the left, prompt text on the right
 row 3   cell content line: the raw text of the highlighted cell (full text)
 row 4   column headers A B C ... in reverse video (current column bold)
 rows 5-22  worksheet cells, 7-char row-number gutter on the left
 row 23  indicator line: CAPS NUM SCROLL CALC MEM OVR/INS, clock on the right (MEM lights when the workbook exceeds ~512 KB of cell storage)
 row 24  message line
 row 25  error line (red on white)
```

If the terminal is smaller than 80x25, Descartes announces
`Terminal must be at least 80x25, current NxM` and refuses to start.

## 2. Modes

Descartes is a two-mode-plus state machine, exactly like 1-2-3:

- **READY** — the cursor moves; typing starts an entry; `/` opens the menu.
- **VALUE / LABEL** — you are typing a number/formula or a text label; the entry appears
  on the control panel; Enter commits, Esc aborts.
- **EDIT** — F2 (or double-click) loads the current cell's raw text for editing.
- **POINT** — a command needs a range: the cursor moves, `.` anchors and extends a range,
  Enter submits, Esc cancels. A mouse click also submits.
- **MENU** — the slash menu tree is open (see §5).
- **DIALOG** — a prompt chain is asking for arguments (see §5.3).
- **ERROR** — an error message is shown; any key returns to READY.

## 3. Moving around and entering data

| Key | Action |
| --- | --- |
| Arrow keys | move the cell cursor one cell |
| PgUp / PgDn | move 18 rows |
| Ctrl+PgUp / Ctrl+PgDn | previous / next worksheet (wraps) |
| Home / End | top of column / bottom of column |
| Enter | commit an entry (then move down), or move down in READY |
| Tab / Shift-Tab | move right / left |
| `~` | jump to A1 |
| F9 | force a full recalculation |
| Esc | cancel the current entry or command |
| Backspace | in an entry: delete a character; in a prompt chain: previous prompt |
| Ctrl+C | CANCELED — unwind everything back to READY |
| Ctrl+Q / Alt+Q | quit (the terminal is restored: echo, cursor, mouse protocol off) |

### 3.1 What gets stored

- An entry beginning with `0-9 + - . ( @ # $` becomes a **NUMBER** (if it is a plain
  number, with an optional `%` suffix: `50%` is stored as 0.5) or a **FORMULA**.
- Any other text is a **LABEL**. The first character may be a label prefix:
  `' ` left-align, `^` center, `"` right-align (exactly as 1-2-3).
- Formulas start with `+` or `-` (or `(`, `@`, `$`, ...). Example: `+A1*2`,
  `@SUM(A1..A2)`.
- Ranges are written `A1..B2`.
- `Del` in READY erases the current cell; F2 edits it.

### 3.2 Formulas

Full syntax and the complete `@function` list are in the
[reference manual](reference-manual.md#4-formula-language). Examples:

```
+Sales*0.08
@SUM(A1..A10)
@IF(B1>100,"big","small")
@VLOOKUP(A1,PriceList,2)
@DATE(2024,1,1)
@SUM(A:C!A1..A10)     <- 3-D: sums A1..A10 on every sheet from A through C
@SUM(TOTAL)           <- named range (created with /RANGE NAME CREATE)
+"Total: "&@STRING(@SUM(A1..A10),2)
```

Recalculation is automatic; use `/WORKSHEET GLOBAL RECALCULATION MANUAL` to switch to
manual (the **CALC** indicator lights while cells are dirty) and F9 to recalculate.
`/WORKSHEET GLOBAL RECALCULATION ITERATION n` enables iterative calculation of circular
references (without it, circular references show `ERR`).

## 4. Copying and moving

`/COPY` and `/MOVE` take a source range and a destination anchor:

1. `/` → `COPY`
2. `Copy from:` — type `A1..B2` or press Enter and pick with the cursor in POINT mode
   (`.` anchors, arrows extend, Enter submits)
3. `To:` — type the destination anchor cell, e.g. `D1`

Formulas are copied with **relative** references adjusted (`+A1*2` copied one column
right becomes `+B1*2`); absolute references marked with `$` (e.g. `+$A$1`) stay fixed.
If the destination is a range whose size is a multiple of the source, the source is
tiled (repeat-replication). `/MOVE` additionally clears the source cells.

## 5. The slash menu

Press `/` (or F10, or click the menu bar). The top-level order is:

```
WORKSHEET  RANGE  COPY  MOVE  FILE  PRINT  GRAPH  DATA  SYSTEM  ADD-IN
```

- `←`/`→` move across the top level, `↑`/`↓` within a dropdown, Enter or a capital
  letter selects; unselected open dropdowns stay visible.
- Submenus cascade to the right of the parent item.
- Esc/Backspace backs out one level; a second `/` closes the tree.

### 5.1 Prompt chains

Commands that need arguments ask on the control panel, one prompt at a time, with the
current entry reverse-highlighted:

- **Enter** accepts the current prompt and advances (range/cell prompts can also be
  answered by pressing Enter on an empty line and pointing with the cursor).
- **Esc** cancels the whole chain.
- **Backspace** re-enters the previous prompt.

### 5.2 The command tree (summary)

- **WORKSHEET** — GLOBAL (LABEL-PREFIX, ZERO-SUPPRESS, FORMAT, RECALCULATION
  AUTO/MANUAL/ITERATION, PROTECTION), INSERT/DELETE COLUMN/ROW/SHEET, ERASE,
  COLUMN-WIDTH SET/RESET, HIDE/UNHIDE, WINDOW HORIZONTAL/VERTICAL/CLEAR/SYNC/UNSYNC/CHANGE,
  TITLES BOTH/HORIZONTAL/VERTICAL/CLEAR, STATUS, PAGE
- **RANGE** — FORMAT, ERASE, NAME (CREATE/DELETE/LABELS/RESET/TABLE), JUSTIFY, LABEL,
  BACKSOLVE (solve a formula cell to a target value by adjusting a variable cell),
  PROTECT/UNPROTECT, INPUT, TRANSPOSE, VALUE, SEARCH
- **COPY / MOVE** — source range → destination anchor
- **FILE** — SAVE, LOAD, COMBINE (COPY/ADD/SUBTRACT), XTRACT, ERASE, LIST, IMPORT
  (DIF/TEXT/NUMBERS), DIRECTORY, QUIT (exit; Ctrl+Q/Alt+Q also quits)
- **PRINT** — PRINTER (piped to a command, `$PAGER`, or `DESCARTES_PRINTER`), FILE,
  RANGE, LINES-PAGE, MARGINS, BORDERS, HEADER
- **GRAPH** — TYPE (LINE/BAR/XY/STACKED-BAR/PIE), X-RANGE, A..F series, RESET, VIEW,
  SAVE, OPTIONS (LEGEND/FORMAT/TITLES/GRID/SCALE)
- **DATA** — FILL, TABLE (1/2-variable), SORT (DATA-RANGE, PRIMARY/SECONDARY-KEY, RESET,
  GO), QUERY (INPUT, CRITERION, OUTPUT, UNIQUE, FIND/DELETE/EXTRACT), FREQUENCY,
  REGRESSION
- **SYSTEM** — run a shell command and show its output in a centered bordered window;
  any key returns
- **ADD-IN** — stub: "No add-ins loaded"

The full reference with every prompt is in the
[reference manual](reference-manual.md#7-slash-command-reference).

## 6. Files

- `/FILE SAVE name` writes the native **.vg1** (JSON) format: cell raw text, formats,
  column widths, named ranges, recalc mode, cursor/window state, graph definitions —
  with deterministic ordering for clean diffs. `/FILE LOAD` reads `.vg1`, `.wk1`/`.wks`
  (Lotus 1-2-3 Release 2 binary, formulas best-effort translated from RPN), `.wk3`
  (Release 3.x: **all sheets** are imported, with sheet names, range names incl. 3-D
  names, column widths, and formulas incl. cross-sheet/3-D references; password
  protected files are rejected), `.dif`, and delimited `.txt`.
- `/FILE XTRACT` exports a range as WK1, DIF, or text.
- `/FILE COMBINE COPY|ADD|SUBTRACT` merges another file into the current sheet.
- `/FILE IMPORT DIF|TEXT|NUMBERS` imports at a chosen anchor cell. DIF and text import
  carry numbers and plain text only (no formula reconstruction); imported strings are
  always labels, even if they start with `+` or `=`.
- `/FILE LIST` and `/FILE DIRECTORY` browse the filesystem.

## 7. Printing

`/PRINT FILE name range` writes a text rendering of the range (respecting column widths,
formats, optional borders and header). The range may be 3-D (`A:C!A1..B2`), in which
case each sheet in the span is printed under a `Sheet X` heading. `/PRINT RANGE`
accepts the same 3-D syntax plus a free-text destination (PRINTER, `|pipe`, file name). `/PRINT PRINTER` pipes the same output to a shell
command (`|cmd`), `$PAGER`, or `DESCARTES_PRINTER`. `/PRINT LINES-PAGE`, `MARGINS`,
`BORDERS`, `HEADER` adjust the layout.

## 8. Graphs

```
/GRAPH TYPE BAR
/GRAPH X-RANGE A1..A4        (category labels, optional)
/GRAPH A B1..B4  "Sales"     (up to six series A..F)
/GRAPH VIEW                  (any key returns)
```

Graph types: LINE, BAR, XY, STACKED-BAR, PIE. In the text viewport, bars are drawn with
block characters (`#` on non-UTF-8 terminals), axes with `- | +`, series glyphs
`□ ■ ▲ ▼ ○ *` (ASCII fallback `o # * + x .`). Pie charts render as a horizontal
proportion bar with percentage labels (a documented text-mode compromise). Axis scaling
is automatic with 3-6 gridlines; the legend is beneath the chart. Graph definitions
persist in the .vg1 file.

## 9. Database (/DATA)

- `/DATA FILL range start step [stop]` — fill a range with an arithmetic series.
- `/DATA SORT` — set DATA-RANGE, PRIMARY-KEY (and SECONDARY-KEY) with ascending/descending,
  then GO. The sort is stable and uses the exact 1-2-3 collation (digits before A-Z before
  a-z).
- `/DATA QUERY` — set INPUT (range with a header row), CRITERION (header row + condition
  rows; conditions may be `>10`, `<>x`, wildcards `an*`, or formulas referencing the first
  data row), optionally OUTPUT, then FIND, DELETE, or EXTRACT (with UNIQUE to dedupe).
- `/DATA TABLE 1-VARIABLE / 2-VARIABLE` — what-if data tables.
- `/DATA FREQUENCY data-range bin-range` — counts written to the right of the bins.
- `/DATA REGRESSION x-range y-range output-cell` — writes slope, intercept, R², standard
  errors, n, and means.

## 10. Mouse

The XTerm SGR mouse protocol (1006, with 1000 fallback) is enabled at startup and
disabled on exit. Behaviors:

| Element | Click | Drag | Double-click |
| --- | --- | --- | --- |
| Menu bar item | open dropdown | slide across bar | — |
| Dropdown item | execute | move highlight | — |
| Cell | move cursor (submit in POINT mode) | extend selection | edit cell (F2) |
| Column header | select column | range of columns / drag right edge to resize width | double-click: auto-fit width |
| Row gutter | select row | range of rows | — |
| Content line | F2 into EDIT | — | — |
| Wheel | scroll one row (Ctrl+wheel: horizontal) | autoscroll during drags | — |

Inside tmux >= 3.2 with `set -g mouse on`, 1006 passes through. Terminals without the
protocol simply stay keyboard-only; unexpected mouse bytes never leak into cell entry.

## 11. Help

F1 shows the function-key help screen in a centered bordered window; any key returns.
`/WORKSHEET STATUS` shows the current cell, recalc mode, cell count, and CALC state.

## 12. Macros

A macro is a cell whose text starts with `{`. It runs the 1-2-3 `{...}` command
language: commands in braces, keystrokes as plain text between them.

**Writing and running one:**

1. Enter a macro cell, e.g. in `A1`: `{GOTO B1}{LET B1, 42}{ENTER}`
2. Assign it to a key: `/RANGE NAME CREATE`, name `M`, range `A1`.
3. Press **Alt+M** in READY mode to run it. (Alt+letter with no matching
   single-letter name is passed through as ordinary input.)

**Commands:**

| Command | Effect |
| --- | --- |
| `{GOTO cell}` | move the cursor |
| `{LET cell, value}` / `{PUT ...}` | enter a value/formula/label into a cell (value may be a formula, e.g. `{LET A1, =B1*2}`) |
| `{BEEP}` | audible beep |
| `{RECALC}` | force full recalculation |
| `{WAIT secs}` | pause (capped at 5 s) |
| `{SYSTEM command}` | run a shell command in the SYSTEM window |
| `{BRANCH cell}` | jump to another macro cell (no return) |
| `{CALL cell}` / `{RETURN}` | call a macro as a subroutine |
| `{HALT}` | stop the macro |
| `{QUIT}` | quit the application |
| `{IF expr} ... {ELSE} ... {END}` | conditional block; `expr` is a normal 1-2-3 formula evaluated at the macro cell (nestable) |
| `{LOOK}` | no-op (1-2-3 compatibility) |
| `{INPUT}` | pause; you edit the `/RANGE INPUT` range and press Enter, then the macro continues |
| `{GETNUMBER "prompt", cell}` / `{GETLABEL ...}` | show prompt in the control panel, store your answer (number/text) into `cell`, macro continues |
| `{MENUBRANCH cell}` | feed another cell's text as menu keystrokes (e.g. `'/FR`) |
| `{DEFINE name:range}` | create a range name at runtime (3-D ranges allowed: `{DEFINE ALL: A:B!A1..A1}`) |
| `{FOR counter, start, end[, step]} ... {FOREND}` | loop; the counter cell is set each iteration; `{FORBREAK}` exits early; loops nest |

**Special keys:** `{LEFT} {RIGHT} {UP} {DOWN} {HOME} {ENDKEY} {PGUP} {PGDN} {INS}
{DEL} {BS} {TAB} {ENTER} {ESC} {F1}`–`{F12}`. Note `{END}` closes an `IF` block;
use `{ENDKEY}` for the End key. Any text outside braces is typed into the app
keystroke by keystroke, so a macro can drive menus, entry, and navigation, e.g.
`{F2}{BS}{BS}7{ENTER}`.

**Limits:** macros are capped at 10000 steps and 16 `CALL`/`BRANCH` depth levels,
so a runaway macro stops with a `Macro:` error instead of hanging. Macro cells
are ordinary label cells and save/load with `.vg1` like anything else.
