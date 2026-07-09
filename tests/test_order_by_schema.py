"""Validation of the OrderBySchema and order_by on definition schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.sql_definition import OrderBySchema, SqlDefinitionCreate


def test_valid_order_by():
    o = OrderBySchema(column="LastUpdateTime", direction="DESC")
    assert o.column == "LastUpdateTime"
    assert o.direction == "DESC"


def test_direction_defaults_to_asc():
    assert OrderBySchema(column="name").direction == "ASC"


def test_invalid_direction_rejected():
    with pytest.raises(ValidationError):
        OrderBySchema(column="name", direction="SIDEWAYS")


@pytest.mark.parametrize("bad", ["a b", "a;b", "a.b", "1col", "col'", "a-b", ""])
def test_invalid_column_rejected(bad):
    with pytest.raises(ValidationError):
        OrderBySchema(column=bad)


def test_definition_create_defaults_empty_order_by():
    d = SqlDefinitionCreate(name="q", sql="SELECT 1")
    assert d.order_by == []


def test_definition_create_accepts_order_by():
    d = SqlDefinitionCreate(
        name="q",
        sql="SELECT 1",
        order_by=[{"column": "a", "direction": "DESC"}, {"column": "b"}],
    )
    assert d.order_by[0].column == "a"
    assert d.order_by[1].direction == "ASC"
