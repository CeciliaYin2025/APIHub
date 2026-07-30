"""ORM model for per-request audit trail (platform PostgreSQL store)."""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "api_hub_audit_logs"

    # Composite indexes for the usage report's filter dimensions. create_all
    # builds these on fresh installs; scripts/init_db.py adds them to
    # pre-existing tables (create_all skips tables that already exist).
    __table_args__ = (
        Index("ix_audit_principal_ts", "principal", "ts"),
        Index("ix_audit_db_alias_ts", "db_alias", "ts"),
        Index("ix_audit_action_ts", "action", "ts"),
        Index("ix_audit_action_type_ts", "action_type", "ts"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(DateTime, server_default=func.now(), index=True)
    principal_type = Column(String(20))  # "user" | "api_key" | "anonymous"
    principal = Column(String(100), index=True)
    api_key_id = Column(Integer, nullable=True)
    api_key_name = Column(String(100), nullable=True)
    client_ip = Column(String(64))
    method = Column(String(10))
    path = Column(String(500))
    db_alias = Column(String(64), nullable=True)
    action = Column(String(200), nullable=True)
    status_code = Column(Integer)
    duration_ms = Column(Integer)
    # "query" | "procedure" | "write" | "definition" | "report" — lets the
    # usage report separate data-access volume from admin operations.
    action_type = Column(String(20), nullable=True)
    # Request parameters, stored unmasked and untruncated by design. Table
    # size is bounded by AUDIT_RETENTION_DAYS + scripts/purge_audit_logs.
    params = Column(JSONB, nullable=True)
    result_rows = Column(Integer, nullable=True)
