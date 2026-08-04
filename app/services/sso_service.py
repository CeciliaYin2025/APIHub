"""Resolve an SSO-verified identity to a local platform-store user.

Shared by both SSO entry points — the MCP identity bridge
(``POST /api/v1/auth/sso-token``) and the Admin UI callback
(``GET /api/v1/auth/sso/callback``) — so the provisioning policy cannot drift
between them: a user MCP lets in is exactly a user the Admin UI lets in.

Neither entry point verifies identity here. The bridge has already checked its
shared secret and the Admin UI has already exchanged a single-use auth code;
this module trusts the username it is handed.
"""

import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.user import User


class SsoExchangeError(Exception):
    """Auth-code exchange with isap-auth failed.

    ``reason`` is a stable slug the Admin UI callback turns into a
    ``#sso_error=`` fragment, so it must stay in sync with the table in
    app/admin/js/app.js.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def autocreate_enabled() -> bool:
    """SSO_AUTOCREATE, honouring the legacy MCP_BRIDGE_AUTOCREATE alias.

    OR rather than fallback: a site that already enabled the MCP-only flag keeps
    working after upgrading, without having to learn the new name.
    """
    return settings.sso_autocreate or settings.mcp_bridge_autocreate


def resolve_sso_user(db: Session, username: str, email: str = "") -> User:
    """Return the local User for an already-verified SSO identity.

    ``email`` is accepted because both callers have it, but User has no email
    column — it is deliberately unused rather than silently dropped at the call
    site. Raises HTTPException(403) when the account may not use SSO.
    """
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        if not autocreate_enabled():
            raise HTTPException(status_code=403, detail="User not provisioned")
        # User has no `email` column; SSO users get a valid-but-unguessable hash
        # so password login stays a generic 401 (verify("") would raise instead).
        user = User(
            username=username,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            is_active=True,
            is_superuser=False,
            is_service_account=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    if user.is_service_account or not user.is_active:
        raise HTTPException(status_code=403, detail="Account not eligible for SSO bridge")
    return user


def exchange_sso_code(code: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Trade a one-time auth code for isap-auth's user info (legacy JSON style).

    Uses ISAP_AUTH_INTERNAL_URL so the call stays on the docker network instead
    of looping back out through the public gateway. ``transport`` exists for
    tests (httpx.MockTransport).
    """
    url = f"{settings.isap_auth_internal_url.rstrip('/')}/api/v1/token"
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            resp = client.post(url, json={"code": code, "app_id": settings.isap_app_id})
    except httpx.HTTPError as e:
        raise SsoExchangeError("auth_unreachable") from e

    if resp.status_code != 200:
        raise SsoExchangeError("invalid_code")
    try:
        data = (resp.json() or {}).get("data") or {}
    except ValueError as e:
        raise SsoExchangeError("invalid_code") from e
    if not data.get("username"):
        raise SsoExchangeError("invalid_code")
    return data
