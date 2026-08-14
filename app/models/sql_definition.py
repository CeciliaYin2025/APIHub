"""ORM model for SQL definitions stored in PostgreSQL."""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, func
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
    # Free-text summary of the result set (typically the column list). Read by
    # LLMs through the MCP tool description; without it a caller has to run the
    # query once just to learn what comes back.
    result_description = Column(Text, nullable=False, server_default="")
    sql = Column(Text, nullable=False)
    permission = Column(String(200), nullable=False)
    # Take a definition out of service without deleting it. Disabled definitions
    # stay in the registry so the Admin UI can switch them back on and execution
    # can say "disabled" rather than the indistinguishable "not found", but they
    # are excluded from the definition list and therefore from the MCP catalog.
    # Defaults true so existing rows keep working across the migration.
    enabled = Column(Boolean, nullable=False, server_default="true", index=True)
    params = Column(JSONB, default=dict)
    order_by = Column(JSONB, default=list)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
