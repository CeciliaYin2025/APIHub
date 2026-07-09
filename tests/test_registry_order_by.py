"""SqlDefinition dataclass carries order_by; loaders populate it."""

from app.services.sql_registry import SqlDefinition


def test_dataclass_defaults_empty():
    d = SqlDefinition(name="q", description="", sql="SELECT 1", permission="query:q")
    assert d.order_by == []


def test_dataclass_accepts_order_by():
    d = SqlDefinition(
        name="q", description="", sql="SELECT 1", permission="query:q", order_by=[{"column": "a", "direction": "DESC"}]
    )
    assert d.order_by[0]["column"] == "a"
