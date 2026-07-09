"""Resolve caller credentials once per request and attach to request.state.

Runs before the rate limiter and routers so both can read the principal.
A present-but-invalid API key short-circuits with a JSON error; missing or
invalid JWT leaves principal=None for the dependency layer to reject.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.db.session import SessionLocal
from app.services.auth_service import ApiKeyError, Principal, resolve_principal


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


class AuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            request.state.principal = build_principal(request)
        except ApiKeyError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)
