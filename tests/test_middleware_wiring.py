"""App wires the three new middlewares in the correct order."""

from app.main import app
from app.middleware.audit import AuditMiddleware
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.rate_limiter import RateLimiterMiddleware


def _classes():
    # app.user_middleware is ordered outermost-first (last added = index 0).
    return [m.cls for m in app.user_middleware]


def test_all_three_present():
    classes = _classes()
    for cls in (AuditMiddleware, AuthContextMiddleware, RateLimiterMiddleware):
        assert cls in classes


def test_audit_outer_authcontext_outer_ratelimiter():
    classes = _classes()
    assert classes.index(AuditMiddleware) < classes.index(AuthContextMiddleware)
    assert classes.index(AuthContextMiddleware) < classes.index(RateLimiterMiddleware)
