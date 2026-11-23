import pytest

from descartes.grid import (EMPTY, FORMULA, LABEL, NUMBER, Cell, Worksheet,
                             classify_entry, col_index, col_letters,
                             format_address, format_range, normalize_corners,
                             parse_address, parse_number_literal, range_cells,
                             split_range)


class TestAddressing:
    def test_col_letters_roundtrip(self):
        assert col_letters(0) == "A"
        assert col_letters(25) == "Z"
        assert col_letters(26) == "AA"
        assert col_letters(255) == "IV"
        for i in range(256):
            assert col_index(col_letters(i)) == i

    def test_parse_address(self):
        assert parse_address("A1") == (0, 0)
        assert parse_address("$IV$8192") == (255, 8191)
        assert parse_address("b12") == (1, 11)
        with pytest.raises(ValueError):
            parse_address("A0")
        with pytest.raises(ValueError):
            parse_address("IW1")
        with pytest.raises(ValueError):
            parse_address("1A")

    def test_format_address(self):
        assert format_address(0, 0) == "A1"
        assert format_address(255, 8191) == "IV8192"

    def test_split_range_normalizes(self):
        assert split_range("B2..A1") == ((0, 0), (1, 1))
        assert split_range("C5") == ((2, 4), (2, 4))
        assert format_range((0, 0), (1, 2)) == "A1..B3"

    def test_range_cells_row_major(self):
        assert list(range_cells((0, 0), (1, 1))) == [(0, 0), (1, 0), (0, 1), (1, 1)]


class TestClassify:
    def test_number(self):
        assert classify_entry("123") == (NUMBER, None, 123.0)
        assert classify_entry("-4.5") == (NUMBER, None, -4.5)
        assert classify_entry("+7") == (NUMBER, None, 7.0)
        assert classify_entry("50%") == (NUMBER, None, 0.5)

    def test_formula(self):
        assert classify_entry("@SUM(A1..A2)")[0] == FORMULA
        assert classify_entry("+A1*2")[0] == FORMULA
        assert classify_entry("(1+2)")[0] == FORMULA
        assert classify_entry("$A$1")[0] == FORMULA

    def test_label(self):
        assert classify_entry("hello") == (LABEL, "left", None)
        assert classify_entry("'right")[0] == LABEL
        assert classify_entry("'right")[1] == "left"
        assert classify_entry('"right"')[1] == "right"
        assert classify_entry("^center")[1] == "center"

    def test_empty(self):
        assert classify_entry("") == (EMPTY, None, None)
        assert classify_entry(None) == (EMPTY, None, None)

    def test_parse_number_literal(self):
        assert parse_number_literal("1e3") == 1000.0
        assert parse_number_literal("25%") == 0.25
        assert parse_number_literal("abc") is None


class TestWorksheet:
    def test_set_entry_kinds(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "42")
        ws.set_entry(1, 0, "text")
        ws.set_entry(2, 0, "+A1*2")
        assert ws.kind_at(0, 0) == NUMBER
        assert ws.kind_at(1, 0) == LABEL
        assert ws.kind_at(2, 0) == FORMULA
        assert ws.value_at(0, 0) == 42.0
        assert ws.value_at(1, 0) == "text"

    def test_set_entry_preserves_fmt(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "1")
        ws.get_or_make(0, 0).fmt = {"type": "fixed", "decimals": 3}
        ws.set_entry(0, 0, "2")
        assert ws.cell(0, 0).fmt == {"type": "fixed", "decimals": 3}

    def test_clear(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "1")
        ws.set_entry(0, 0, "")
        assert ws.cell(0, 0) is None

    def test_col_width(self):
        ws = Worksheet()
        assert ws.col_width(5) == 9
        ws.set_col_width(5, 20)
        assert ws.col_width(5) == 20
        ws.set_col_width(5, 9)
        assert ws.col_width(5) == 9
        ws.set_col_width(0, 999)
        assert ws.col_width(0) == 240

    def test_insert_delete_cols_shifts_cells(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "1")
        ws.set_entry(2, 0, "3")
        ws.set_col_width(2, 15)
        ws.insert_cols(1, 1)
        assert ws.value_at(0, 0) == 1.0
        assert ws.value_at(3, 0) == 3.0
        assert ws.col_width(3) == 15
        ws.delete_cols(1, 1)
        assert ws.value_at(2, 0) == 3.0
        assert ws.col_width(2) == 15

    def test_insert_delete_rows(self):
        ws = Worksheet()
        ws.set_entry(0, 0, "1")
        ws.set_entry(0, 2, "3")
        ws.insert_rows(1, 1)
        assert ws.value_at(0, 3) == 3.0
        ws.delete_rows(1, 1)
        assert ws.value_at(0, 2) == 3.0

    def test_cell_dict_roundtrip(self):
        c = Cell(raw="+A1", kind=FORMULA, fmt={"type": "fixed", "decimals": 1},
                 protected=True, hidden=True, align="right")
        d = c.to_dict(3, 4)
        c2 = Cell.from_dict(d)
        assert c2.raw == "+A1" and c2.kind == FORMULA and c2.fmt == c.fmt
        assert c2.protected and c2.hidden and c2.align == "right"
