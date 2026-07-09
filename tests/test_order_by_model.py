"""SqlDefinitionModel exposes an order_by JSONB column."""

from app.models.sql_definition import SqlDefinitionModel


def test_model_has_order_by_column():
    row = SqlDefinitionModel(
        name="q",
        db_alias="default",
        sql="SELECT 1",
        permission="query:q",
        order_by=[{"column": "a", "direction": "DESC"}],
    )
    assert row.order_by == [{"column": "a", "direction": "DESC"}]
    assert "order_by" in SqlDefinitionModel.__table__.columns
