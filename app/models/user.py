"""ORM models for users, roles, and API endpoint permissions."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.session import Base

# Many-to-many: user <-> role
user_roles = Table(
    "api_hub_user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("api_hub_users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("api_hub_roles.id"), primary_key=True),
)

# Many-to-many: role <-> permission
role_permissions = Table(
    "api_hub_role_perms",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("api_hub_roles.id"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("api_hub_permissions.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "api_hub_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200))
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_service_account = Column(Boolean, default=False, nullable=False)
    # "local" | "sso" — how this account proves who it is. Governs PASSWORD
    # login only: an "sso" account cannot use POST /auth/token. It deliberately
    # does NOT gate the SSO side, because every pre-existing row was backfilled
    # to "local" and locking those users out of SSO would be a silent outage.
    auth_source = Column(String(20), default="local", server_default="local", nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    roles = relationship("Role", secondary=user_roles, back_populates="users", lazy="joined")


class Role(Base):
    __tablename__ = "api_hub_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles", lazy="joined")


class Permission(Base):
    __tablename__ = "api_hub_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource = Column(String(200), nullable=False)  # e.g. "query:employees" or "procedure:get_salary"
    action = Column(String(50), nullable=False)  # "read", "execute", "write"
    description = Column(Text)

    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")

    __table_args__ = (
        # unique constraint on (resource, action)
        {"info": {"unique_constraints": [("resource", "action")]}},
    )


class ApiKey(Base):
    __tablename__ = "api_hub_api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("api_hub_users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    key_prefix = Column(String(16), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    ip_allowlist = Column(JSONB, nullable=True)  # list[str] of CIDRs; null/empty = unrestricted
    rate_limit_per_minute = Column(Integer, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", lazy="joined")
