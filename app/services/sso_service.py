"""Resolve an SSO-verified identity to a local platform-store user.

Shared by both SSO entry points — the MCP identity bridge
(``POST /api/v1/auth/sso-token``) and the Admin UI callback
(``GET /api/v1/auth/sso/callback``) — so the provisioning policy cannot drift
between them: a user MCP lets in is exactly a user the Admin UI lets in.

Neither entry point verifies identity here. The bridge has already checked its
shared secret and the Admin UI has already redeemed a single-use auth code;
this module trusts the username it is handed.

It also owns the Admin UI's half of the OAuth 2.1 authorization-code flow
against isap-auth: PKCE pair generation, the code and refresh grants, and the
UserInfo lookup that turns an access token back into a username.
"""

import base64
import hashlib
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


# openid/profile/email identify the user; offline_access is what earns a refresh
# token from isap-auth, and without it the Admin UI session dies with the access
# token and the user has to run the whole browser login again.
SCOPE = "openid profile email offline_access"


def build_pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge).

    PKCE binds the authorization code to the browser that started the flow: a
    code stolen in transit (proxy log, referrer, shoulder-surfed redirect) is
    useless without the verifier, which never leaves this server. The legacy
    app-code flow this replaces had no such binding.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _token_endpoint() -> str:
    """isap-auth's token endpoint, reached over the docker network.

    ISAP_AUTH_INTERNAL_URL keeps this server-to-server leg off the public
    gateway; only the browser-facing /authorize redirect uses the public URL.
    """
    return f"{settings.isap_auth_internal_url.rstrip('/')}/api/v1/token"


def _post_token(form: dict, failure: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """POST an OAuth 2.1 grant and return the token response.

    ``failure`` is the slug raised for anything the authorization server
    refuses, so the caller can tell "this code is bad" from "this refresh token
    is spent" without inspecting the response. ``transport`` exists for tests.
    """
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            resp = client.post(_token_endpoint(), data=form)
    except httpx.HTTPError as e:
        raise SsoExchangeError("auth_unreachable") from e

    if resp.status_code != 200:
        raise SsoExchangeError(failure)
    try:
        data = resp.json()
    except ValueError as e:
        raise SsoExchangeError(failure) from e
    if not data.get("access_token"):
        raise SsoExchangeError(failure)
    return data


def exchange_pkce_code(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Redeem an authorization code (OAuth 2.1 + PKCE) for tokens."""
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.isap_app_id,
            "code_verifier": verifier,
        },
        "invalid_code",
        transport=transport,
    )


def refresh_tokens(refresh_token: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Rotate a refresh token into a fresh access token.

    isap-auth rotates on every redeem and revokes the whole family on replay,
    so the new refresh token in the response MUST replace the stored one — drop
    it and the next attempt looks like a replay and logs the user out.
    """
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.isap_app_id,
        },
        "session_expired",
        transport=transport,
    )


def fetch_userinfo(access_token: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Resolve an access token to identity claims (OIDC UserInfo).

    The OAuth token grant returns tokens, not a username — unlike the legacy
    JSON exchange it replaces. UserInfo is used rather than introspection
    because it needs no shared secret, so Admin UI SSO stays independent of
    whether OAUTH_RS_ENABLED is configured.
    """
    url = f"{settings.isap_auth_internal_url.rstrip('/')}/api/v1/userinfo"
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as e:
        raise SsoExchangeError("auth_unreachable") from e
    if resp.status_code != 200:
        raise SsoExchangeError("invalid_code")
    try:
        claims = resp.json() or {}
    except ValueError as e:
        raise SsoExchangeError("invalid_code") from e
    username = claims.get("preferred_username") or claims.get("username") or claims.get("sub")
    if not username:
        raise SsoExchangeError("invalid_code")
    claims["username"] = username
    return claims
