"""ORM model for SQL definitions stored in PostgreSQL."""

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import Base


class SqlDefinitionModel(Base):
    __tablename__ = "api_hub_sql_definitions"
    __table_args__ = (UniqueConstraint("name", "db_alias", name="uq_sql_def_name_db_alias"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    db_alias = Column(String(64), nullable=False, server_default="default", index=True)
    # Free-text category used to group definitions in the Admin UI. Named
    # group_name rather than group because GROUP is a reserved SQL keyword.
    group_name = Column(String(100), nullable=False, server_default="", index=True)
    description = Column(Text, default="")
    sql = Column(Text, nullable=False)
    permission = Column(String(200), nullable=False)
    params = Column(JSONB, default=dict)
    order_by = Column(JSONB, default=list)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
