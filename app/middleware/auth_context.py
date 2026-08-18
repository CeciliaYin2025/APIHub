"""Resolve caller credentials once per request and attach to request.state.

Runs before the rate limiter and routers so both can read the principal.
A present-but-invalid API key short-circuits with a JSON error; missing or
invalid JWT leaves principal=None for the dependency layer to reject.
"""

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.security import API_KEY_PREFIX
from app.db.session import SessionLocal
from app.services import oauth_rs
from app.services.auth_service import ApiKeyError, Principal, bearer_token, resolve_principal
from app.services.sso_service import resolve_sso_user


def build_principal(request: Request) -> Principal | None:
    """Open a short-lived session, resolve the principal, and detach it.

    expire_on_commit=False + eager-joined roles/permissions keep the returned
    User usable after the session closes (no lazy loads downstream).
    """
    db = SessionLocal()
    db.expire_on_commit = False
    try:
        return resolve_principal(db, request)
    finally:
        db.close()


def build_sso_principal(username: str, email: str) -> Principal:
    """Map already-verified OAuth claims onto a local user.

    Only the identity is taken from the token. Authorization comes entirely
    from the local roles/permissions attached to that user — an ``is_superuser``
    claim from isap-auth grants nothing here, and must never be wired through.
    """
    db = SessionLocal()
    db.expire_on_commit = False
    try:
        return Principal(type="sso_user", user=resolve_sso_user(db, username, email))
    finally:
        db.close()


async def resolve_oauth_principal(request: Request) -> Principal | None:
    """Validate an isap-auth bearer token and resolve it to a local user.

    Reached only after build_principal has given up, so API keys and local
    password JWTs never cost an introspection round-trip. isap-auth's tokens
    are opaque, so this is a network call — it is awaited rather than run
    inside the synchronous build_principal, which would block the event loop
    for the duration of the request to the authorization server.
    """
    token = bearer_token(request)
    if not token or token.startswith(API_KEY_PREFIX):
        return None
    claims = await oauth_rs.introspect(token)
    if not claims:
        return None
    username = claims.get("username") or claims.get("sub") or ""
    if not username:
        return None
    return build_sso_principal(username, claims.get("email") or "")


class AuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            principal = build_principal(request)
            if principal is None and oauth_rs.enabled():
                principal = await resolve_oauth_principal(request)
        except ApiKeyError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except HTTPException as exc:
            # resolve_sso_user's 403 for an unprovisioned or ineligible account.
            # Surfaced as-is rather than degraded to principal=None: the caller's
            # token is fine, their API Hub account is what is missing, and a bare
            # 401 would send them off debugging the wrong half of the system.
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers={"WWW-Authenticate": oauth_rs.challenge_header()},
            )
        request.state.principal = principal
        return await call_next(request)
