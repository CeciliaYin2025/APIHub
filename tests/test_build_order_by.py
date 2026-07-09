"""Execution-time order_by string builder — re-validates against injection."""

import pytest

from app.services.query_service import _build_order_by


class _Def:
    def __init__(self, order_by):
        self.order_by = order_by


def test_empty_returns_none():
    assert _build_order_by(_Def([])) is None
    assert _build_order_by(_Def(None)) is None


def test_builds_string():
    d = _Def([{"column": "PROJECT_ID", "direction": "ASC"}, {"column": "NAME", "direction": "DESC"}])
    assert _build_order_by(d) == "PROJECT_ID ASC, NAME DESC"


def test_direction_defaulted_and_uppercased():
    assert _build_order_by(_Def([{"column": "a"}])) == "a ASC"
    assert _build_order_by(_Def([{"column": "a", "direction": "desc"}])) == "a DESC"


@pytest.mark.parametrize("bad", ["a; DROP TABLE t", "a b", "a.b", "1a", "col'", "a-b", ""])
def test_invalid_column_raises(bad):
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": bad, "direction": "ASC"}]))


def test_invalid_direction_raises():
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": "a", "direction": "SIDEWAYS"}]))


def test_non_string_column_raises():
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": 123, "direction": "ASC"}]))


def test_non_string_direction_raises():
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": "a", "direction": 5}]))


def test_trailing_newline_column_rejected():
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": "a\n", "direction": "ASC"}]))
