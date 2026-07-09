"""ORM model for per-request audit trail (platform PostgreSQL store)."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "api_hub_audit_logs"

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
