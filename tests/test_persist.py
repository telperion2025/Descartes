import json
import struct

import pytest

from descartes import persist
from descartes.engine import Engine
from descartes.grid import FORMULA, LABEL, NUMBER, Worksheet, parse_address
from descartes.persist import (attach_engine, load_vg1, read_dif, read_wk1,
                                save_vg1, write_dif, write_wk1)


def build_ws():
    ws = Worksheet("A")
    ws.set_entry(0, 0, "123")
    ws.set_entry(1, 0, "'Label")
    ws.set_entry(2, 0, "+A1*2")
    ws.set_entry(0, 1, "3.5")
    ws.set_col_width(1, 14)
    ws.get_or_make(0, 1).fmt = {"type": "fixed", "decimals": 2}
    eng = Engine(ws)
    eng.register_all()
    eng.recalc(force=True)
    return ws, eng


class TestVg1:
    def test_roundtrip(self, tmp_path):
        ws, eng = build_ws()
        path = tmp_path / "t.vg1"
        save_vg1(str(path), {"A": ws},
                 names={"TOTAL": {"sheet": "A", "range": ((0, 0), (2, 0))}},
                 cursor=(2, 0), recalc_mode="auto", iterations=0,
                 graphs=[{"type": "BAR", "x_range": None, "series": [], "title": "t", "options": {}}],
                 print_settings={"lines_page": 66})
        data = load_vg1(str(path))
        ws2 = data["sheets"]["A"]
        assert ws2.value_at(0, 0) == 123.0
        assert ws2.raw_at(2, 0) == "+A1*2"
        assert ws2.kind_at(2, 0) == FORMULA
        assert ws2.value_at(1, 0) == "Label"
        assert ws2.kind_at(1, 0) == LABEL
        assert ws2.col_width(1) == 14
        assert ws2.cell(0, 1).fmt == {"type": "fixed", "decimals": 2}
        assert data["names"]["TOTAL"]["range"] == ((0, 0), (2, 0))
        assert data["cursor"] == (2, 0)
        assert data["graphs"][0]["type"] == "BAR"

    def test_deterministic_order(self, tmp_path):
        ws, eng = build_ws()
        p1 = tmp_path / "a.vg1"
        p2 = tmp_path / "b.vg1"
        save_vg1(str(p1), {"A": ws})
        save_vg1(str(p2), {"A": ws})
        assert p1.read_bytes() == p2.read_bytes()
        doc = json.loads(p1.read_text())
        cells = doc["sheets"]["A"]["cells"]
        keys = [(c["r"], c["c"]) for c in cells]
        assert keys == sorted(keys)

    def test_formula_value_recomputed_on_load(self, tmp_path):
        ws, eng = build_ws()
        path = tmp_path / "t.vg1"
        save_vg1(str(path), {"A": ws})
        data = load_vg1(str(path))
        ws2 = data["sheets"]["A"]
        eng2 = attach_engine(ws2)
        assert ws2.value_at(2, 0) == 246.0


class TestWk1:
    def test_write_read_roundtrip(self, tmp_path):
        ws, eng = build_ws()
        path = tmp_path / "t.wk1"
        write_wk1(str(path), ws, eng)
        data = read_wk1(str(path))
        ws2 = data["sheets"]["A"]
        assert ws2.value_at(0, 0) == 123.0
        assert ws2.value_at(1, 0) == "Label"
        assert ws2.col_width(1) == 14
        # formula either translated to text or kept as cached number
        assert ws2.value_at(2, 0) in (246.0,) or ws2.kind_at(2, 0) == FORMULA
        eng2 = attach_engine(ws2)
        assert ws2.value_at(2, 0) == 246.0

    def test_read_handmade_wk1(self, tmp_path):
        # hand-built WK1: BOF, INTEGER at A1=42, NUMBER at B1=2.5, LABEL at C1=hi, EOF
        out = bytearray()
        out += struct.pack("<HHH", 0x0000, 2, 0x0406)
        # cell records: payload = fmt(1), col(2), row(2), ...
        out += struct.pack("<HHBHHh", 0x000D, 7, 0xFF, 0, 0, 42)          # INTEGER A1=42
        out += struct.pack("<HHBHHd", 0x000E, 13, 0xFF, 1, 0, 2.5)         # NUMBER B1=2.5
        label = b"'hi\0"
        out += struct.pack("<HH", 0x000F, 5 + len(label))
        out += struct.pack("<BHH", 0xFF, 2, 0) + label                      # LABEL C1=hi
        # formula at D1: =A1*2  -> tokens: ref A1 rel, const 2, *, end
        tokens = bytes([0x01]) + struct.pack("<HH", ((-3) & 0x3FFF) | 0x8000, (0 & 0x3FFF) | 0x8000)
        tokens += bytes([0x05]) + struct.pack("<h", 2)
        tokens += bytes([0x0B, 0x03])
        payload = struct.pack("<BHHdH", 0xFF, 3, 0, 84.0, len(tokens)) + tokens
        out += struct.pack("<HH", 0x0010, len(payload)) + payload
        out += struct.pack("<HH", 0x0001, 0)
        path = tmp_path / "hand.wk1"
        path.write_bytes(bytes(out))
        data = read_wk1(str(path))
        ws = data["sheets"]["A"]
        assert ws.value_at(0, 0) == 42.0
        assert ws.value_at(1, 0) == 2.5
        assert ws.value_at(2, 0) == "hi"
        eng = attach_engine(ws)
        assert ws.value_at(3, 0) == 84.0
        assert ws.raw_at(3, 0).startswith("+")


class TestDifAndText:
    def test_dif_roundtrip(self, tmp_path):
        ws, eng = build_ws()
        path = tmp_path / "t.dif"
        write_dif(str(path), ws, (0, 0), (2, 1))
        ws2 = read_dif(str(path))
        assert ws2.value_at(0, 0) == 123.0
        assert ws2.value_at(1, 0) == "Label"
        assert ws2.value_at(0, 1) == 3.5

    def test_text_import(self, tmp_path):
        p = tmp_path / "t.txt"
        p.write_text('1,2,"hello",x\n4,5,"a""b",\n')
        ws = persist.read_text_import(str(p))
        assert ws.value_at(0, 0) == 1.0
        assert ws.value_at(2, 0) == "hello"
        assert ws.value_at(3, 0) == "x"
        assert ws.value_at(2, 1) == 'a"b'

    def test_numbers_import(self, tmp_path):
        p = tmp_path / "n.txt"
        p.write_text("1,abc,3\n")
        ws = persist.read_text_import(str(p), numbers_only=True)
        assert ws.value_at(0, 0) == 1.0
        assert ws.cell(1, 0) is None
        assert ws.value_at(2, 0) == 3.0

    def test_import_file_dispatch(self, tmp_path):
        ws, eng = build_ws()
        p = tmp_path / "t.vg1"
        save_vg1(str(p), {"A": ws})
        data = persist.import_file(str(p))
        assert "A" in data["sheets"]
        wk = tmp_path / "t.wk1"
        write_wk1(str(wk), ws, eng)
        assert persist.import_file(str(wk))["sheets"]["A"].value_at(0, 0) == 123.0
