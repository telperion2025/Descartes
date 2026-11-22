import pytest

from descartes.graph import GraphDef, render
from descartes.grid import Worksheet, parse_address, split_range


def make_ws():
    ws = Worksheet()
    data = {"A1": "Q1", "B1": "Q2", "C1": "Q3", "D1": "Q4",
            "A2": "1", "B2": "4", "C2": "2", "D2": "3",
            "A3": "2", "B3": "1", "C3": "5", "D3": "4"}
    for addr, raw in data.items():
        c, r = parse_address(addr)
        ws.set_entry(c, r, raw)
    return ws


def test_bar_render():
    ws = make_ws()
    g = GraphDef("BAR", x_range=split_range("A1..D1"),
                 series=[{"name": "S1", "range": split_range("A2..D2")},
                         {"name": "S2", "range": split_range("A3..D3")}],
                 title="Sales")
    lines = render(g, ws, 60, 14, utf8=False)
    assert len(lines) == 14
    assert all(len(ln) == 60 for ln in lines)
    assert lines[0].strip() == "Sales"
    assert "#" in "".join(lines)          # bars drawn
    assert "+" in "".join(lines)          # x axis
    assert "S1" in lines[-1] and "S2" in lines[-1]  # legend


def test_line_render_ascii_glyphs():
    ws = make_ws()
    g = GraphDef("LINE", x_range=split_range("A1..D1"),
                 series=[{"name": "S1", "range": split_range("A2..D2")}])
    lines = render(g, ws, 50, 12, utf8=False)
    assert any("o" in ln for ln in lines)


def test_utf8_glyphs():
    ws = make_ws()
    g = GraphDef("LINE", series=[{"name": "S1", "range": split_range("A2..D2")}])
    lines = render(g, ws, 50, 12, utf8=True)
    assert any("□" in ln for ln in lines)


def test_pie_render():
    ws = make_ws()
    g = GraphDef("PIE", series=[{"name": "A", "range": split_range("A2..D2")}])
    lines = render(g, ws, 40, 8, utf8=False)
    assert any("100.0%" in ln for ln in lines)
    assert any("+" in ln and "#" in ln for ln in lines)


def test_stacked_bar():
    ws = make_ws()
    g = GraphDef("STACKED-BAR", series=[{"name": "S1", "range": split_range("A2..D2")},
                                        {"name": "S2", "range": split_range("A3..D3")}])
    lines = render(g, ws, 60, 12, utf8=False)
    assert any("#" in ln for ln in lines)


def test_graphdef_dict_roundtrip():
    g = GraphDef("XY", x_range=split_range("A1..A4"),
                 series=[{"name": "s", "range": split_range("B1..B4")}], title="T")
    g2 = GraphDef.from_dict(g.to_dict())
    assert g2.type == "XY" and g2.title == "T"
    assert g2.x_range == g.x_range and g2.series[0]["range"] == g.series[0]["range"]
