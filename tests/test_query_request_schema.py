"""Validation of QueryRequest.fetch_mode, including the count mode."""

import pytest
from pydantic import ValidationError

from app.schemas.query import QueryRequest


def test_count_fetch_mode_accepted():
    req = QueryRequest(query_name="q", fetch_mode="count")
    assert req.fetch_mode == "count"


def test_default_fetch_mode_is_page():
    req = QueryRequest(query_name="q")
    assert req.fetch_mode == "page"


@pytest.mark.parametrize("mode", ["page", "all", "batch", "top_n", "one", "count"])
def test_all_known_fetch_modes_accepted(mode):
    assert QueryRequest(query_name="q", fetch_mode=mode).fetch_mode == mode


def test_unknown_fetch_mode_rejected():
    with pytest.raises(ValidationError):
        QueryRequest(query_name="q", fetch_mode="bogus")
