"""ORM model for named write definitions stored in PostgreSQL.

A write definition is the write-side counterpart of a SQL definition: it pins
the target alias and table, the columns a caller may send, and — for
``replace`` — the key that bounds what a call may delete. The generic
``/writes/batch`` endpoint trusts the caller's ``where``; this does not.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import Base


class WriteDefinitionModel(Base):
    __tablename__ = "api_hub_write_definitions"
    __table_args__ = (UniqueConstraint("name", "db_alias", name="uq_write_def_name_db_alias"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    db_alias = Column(String(64), nullable=False, index=True)
    group_name = Column(String(100), nullable=False, server_default="", index=True)
    description = Column(Text, nullable=False, server_default="")
    table_name = Column(String(128), nullable=False)
    # "insert" appends; "replace" deletes WHERE key_columns = key, then inserts,
    # in one transaction.
    mode = Column(String(20), nullable=False)
    # [{"name", "type": string|number|date|datetime, "required", "description"}]
    columns = Column(JSONB, nullable=False)
    key_columns = Column(JSONB, nullable=False, server_default="[]")
    max_rows = Column(Integer, nullable=False, server_default="10000")
    permission = Column(String(200), nullable=False)
    enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
