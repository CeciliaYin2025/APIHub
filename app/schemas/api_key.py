"""Pydantic schemas for API key management."""

from datetime import datetime

from pydantic import BaseModel


class ApiKeyCreate(BaseModel):
    service_account_username: str
    name: str
    expires_at: datetime | None = None
    ip_allowlist: list[str] | None = None
    rate_limit_per_minute: int | None = None


class ApiKeyOut(BaseModel):
    id: int
    name: str
    service_account: str
    key_prefix: str
    enabled: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    api_key: str  # plaintext, returned once at creation
