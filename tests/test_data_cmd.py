import pytest

from descartes import data_cmd
from descartes.data_cmd import (fill, frequency, lotus_sort_key, query,
                                 regression, sort_range)
from descartes.engine import Engine
from descartes.grid import Worksheet, parse_address, split_range


def make(entries):
    ws = Worksheet()
    for addr, raw in entries.items():
        c, r = parse_address(addr)
        ws.set_entry(c, r, raw)
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws, eng


class TestSortOrder:
    def test_lotus_order(self):
        vals = ["b", "A", "1", "B", "a", 5, 3, "10"]
        s = sorted(vals, key=lotus_sort_key)
        assert s == [3, 5, "1", "10", "A", "B", "a", "b"]

    def test_stable_sort(self):
        ws, eng = make({"A1": "k", "B1": "x", "A2": "b", "B2": "y",
                        "A3": "a", "B3": "z", "A4": "b", "B4": "w"})
        rng = split_range("A1..B4")
        sort_range(ws, eng, rng, [(0, True)])
        assert [ws.value_at(0, r) for r in range(4)] == ["a", "b", "b", "k"]
        assert [ws.value_at(1, r) for r in range(4)] == ["z", "y", "w", "x"]  # stable

    def test_descending(self):
        ws, eng = make({"A1": "1", "A2": "3", "A3": "2"})
        sort_range(ws, eng, split_range("A1..A3"), [(0, False)])
        assert [ws.value_at(0, r) for r in range(3)] == [3.0, 2.0, 1.0]


class TestFill:
    def test_fill(self):
        ws, eng = make({})
        fill(ws, eng, split_range("A1..A5"), 1, 2, 7)
        assert [ws.value_at(0, r) for r in range(5)] == [1.0, 3.0, 5.0, 7.0, None]
        # stop honored
        ws2, eng2 = make({})
        fill(ws2, eng2, split_range("A1..A5"), 1, 2, 6)
        assert [ws2.value_at(0, r) for r in range(5)] == [1.0, 3.0, 5.0, None, None]


class TestQuery:
    def setup_db(self):
        return make({
            "A1": "Name", "B1": "Age", "C1": "City",
            "A2": "ann", "B2": "30", "C2": "NYC",
            "A3": "Bob", "B3": "25", "C3": "LA",
            "A4": "cyd", "B4": "40", "C4": "NYC",
            # criterion: Age >= 30
            "E1": "Age", "E2": ">=30",
        })

    def test_find(self):
        ws, eng = self.setup_db()
        matches = query(ws, eng, split_range("A1..C4"), split_range("E1..E2"))
        assert matches == [1, 3]  # rows 2 and 4 (0-based)

    def test_find_wildcard(self):
        ws, eng = make({
            "A1": "Name", "A2": "ann", "A3": "Bob", "A4": "anne",
            "C1": "Name", "C2": "an*",
        })
        matches = query(ws, eng, split_range("A1..A4"), split_range("C1..C2"))
        assert matches == [1, 3]

    def test_extract(self):
        ws, eng = self.setup_db()
        query(ws, eng, split_range("A1..C4"), split_range("E1..E2"),
              output_rng=split_range("G1..I4"), action="extract")
        assert ws.value_at(6, 0) == "Name"
        assert ws.value_at(6, 1) == "ann"
        assert ws.value_at(6, 2) == "cyd"

    def test_delete(self):
        ws, eng = self.setup_db()
        query(ws, eng, split_range("A1..C4"), split_range("E1..E2"), action="delete")
        assert ws.value_at(0, 1) is None
        assert ws.value_at(0, 2) == "Bob"


class TestFrequency:
    def test_frequency(self):
        ws, eng = make({"A1": "1", "A2": "2", "A3": "3", "A4": "4", "A5": "5",
                        "C1": "2", "C2": "4"})
        counts = frequency(ws, eng, split_range("A1..A5"), split_range("C1..C2"))
        assert counts == [2, 2, 1]
        assert ws.value_at(3, 0) == 2.0  # counts written right of the bin column
        assert ws.value_at(3, 1) == 2.0
        assert ws.value_at(3, 2) == 1.0


class TestRegression:
    def test_regression(self):
        ws, eng = make({"A1": "1", "A2": "2", "A3": "3", "A4": "4",
                        "B1": "2", "B2": "4", "B3": "6", "B4": "8"})
        res = regression(ws, eng, split_range("A1..A4"), split_range("B1..B4"), (3, 0))
        assert res["slope"] == pytest.approx(2.0)
        assert res["intercept"] == pytest.approx(0.0)
        assert res["r2"] == pytest.approx(1.0)
        assert ws.value_at(3, 0) == "Slope"
        assert ws.value_at(4, 0) == 2.0


class TestTable:
    def test_table_1var(self):
        ws, eng = make({"A1": "+B1*2", "A2": "1", "A3": "2", "A4": "3"})
        data_cmd.data_table_1var(ws, eng, (1, 0), (0, 0), split_range("A1..A4"),
                                 [1, 2, 3])
        assert ws.value_at(1, 1) == 2.0
        assert ws.value_at(1, 2) == 4.0
        assert ws.value_at(1, 3) == 6.0
