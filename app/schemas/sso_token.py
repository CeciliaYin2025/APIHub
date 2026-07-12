"""Schemas for the MCP identity-bridge endpoint."""

from pydantic import BaseModel, Field


class SsoTokenRequest(BaseModel):
    """Verified SSO claims forwarded by the MCP Resource Server after introspection."""

    username: str = Field(..., min_length=1)
    email: str = ""


class SsoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
