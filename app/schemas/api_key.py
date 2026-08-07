"""Pydantic schemas for API key management."""

from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    service_account_username: str
    name: str
    expires_at: datetime | None = None
    ip_allowlist: list[str] | None = None
    rate_limit_per_minute: int | None = None


class ApiKeyUpdate(BaseModel):
    """Partial update of a key's restrictions.

    Only the restriction knobs are editable. ``enabled`` and ``name`` are
    deliberately absent: revocation must stay final, since a revoked key's
    secret is treated as compromised and re-enabling it would resurrect it.

    An omitted field is left alone; an explicit ``null`` clears the
    restriction. The endpoint tells the two apart via ``model_fields_set``,
    which is the only way to lift an expiry once one has been set.
    """

    expires_at: datetime | None = None
    ip_allowlist: list[str] | None = None
    rate_limit_per_minute: int | None = Field(default=None, gt=0)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    service_account: str
    key_prefix: str
    enabled: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # Restrictions are part of the read model: a key's blast radius cannot be
    # audited if the only way to see its limits is to open the database.
    ip_allowlist: list[str] | None = None
    rate_limit_per_minute: int | None = None


class ApiKeyCreated(ApiKeyOut):
    api_key: str  # plaintext, returned once at creation
