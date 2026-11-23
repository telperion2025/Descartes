import datetime

import pytest

from descartes import fmt
from descartes.functions import ERR, NA


class TestNumbers:
    def test_general(self):
        assert fmt.display(579.0, None) == "579"
        assert fmt.display(0.5, None) == "0.5"
        assert fmt.display(-12.25, None) == "-12.25"
        assert fmt.display(0.1 + 0.2, None) == "0.3"

    def test_fixed(self):
        assert fmt.display(3.14159, {"type": "fixed", "decimals": 2}) == "3.14"
        assert fmt.display(2.0, {"type": "fixed", "decimals": 3}) == "2.000"

    def test_comma_currency_percent(self):
        assert fmt.display(1234567.891, {"type": "comma", "decimals": 2}) == "1,234,567.89"
        assert fmt.display(1234.5, {"type": "currency", "decimals": 2}) == "$1,234.50"
        assert fmt.display(0.5, {"type": "percent", "decimals": 0}) == "50%"
        assert fmt.display(0.1234, {"type": "percent", "decimals": 1}) == "12.3%"

    def test_sci(self):
        assert fmt.display(123456.0, {"type": "sci", "decimals": 2}) == "1.23E+05"
        assert fmt.display(0.00012, {"type": "sci", "decimals": 1}) == "1.2E-04"

    def test_text_and_hidden(self):
        assert fmt.display(12.0, {"type": "text"}) == "12"
        assert fmt.display(12.0, {"type": "hidden"}) == ""
        assert fmt.display("abc", {"type": "hidden"}) == ""

    def test_errors_and_blanks(self):
        assert fmt.display(ERR, None) == "ERR"
        assert fmt.display(NA, None) == "NA"
        assert fmt.display(None, None) == ""
        assert fmt.display("hello", None) == "hello"


class TestDates:
    def test_lotus_epoch(self):
        assert fmt.lotus_to_date(1) == datetime.date(1900, 1, 1)
        assert fmt.lotus_to_date(61) == datetime.date(1900, 3, 1)
        assert fmt.date_to_lotus(datetime.date(1900, 1, 1)) == 1
        assert fmt.date_to_lotus(datetime.date(1900, 3, 1)) == 61
        assert fmt.date_to_lotus(datetime.date(2024, 1, 1)) == 45292
        assert fmt.lotus_to_date(45292) == datetime.date(2024, 1, 1)

    def test_date_formats(self):
        s = fmt.date_to_lotus(datetime.date(1992, 1, 5))
        assert fmt.format_date(s, "dd-mmm-yy") == "05-Jan-92"
        assert fmt.format_date(s, "dd-mmm") == "05-Jan"
        assert fmt.format_date(s, "mmm-yy") == "Jan-92"
        assert fmt.format_date(s, "dd/mm/yy") == "05/01/92"

    def test_time_formats(self):
        assert fmt.format_time(0.5, "hh:mm:ss") == "12:00:00"
        assert fmt.format_time(0.5, "hh:mm") == "12:00"


class TestWk1FormatByte:
    def test_roundtrip(self):
        for f in [None, {"type": "fixed", "decimals": 2}, {"type": "sci", "decimals": 3},
                  {"type": "currency", "decimals": 2}, {"type": "percent", "decimals": 1},
                  {"type": "comma", "decimals": 0}, {"type": "date", "style": "dd-mmm-yy"},
                  {"type": "time", "style": "hh:mm:ss"}, {"type": "text"}, {"type": "hidden"}]:
            b = fmt.fmt_to_wk1_byte(f)
            back = fmt.wk1_byte_to_fmt(b)
            if f is None:
                assert back is None
            else:
                assert back is not None and back["type"] == f["type"]
