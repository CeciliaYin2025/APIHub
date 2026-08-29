"""App wires the per-request middlewares in the correct order."""

from app.main import app
from app.middleware.audit import AuditMiddleware
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.concurrency_limiter import ConcurrencyLimiterMiddleware
from app.middleware.rate_limiter import RateLimiterMiddleware


def _classes():
    # app.user_middleware is ordered outermost-first (last added = index 0).
    return [m.cls for m in app.user_middleware]


def test_all_present():
    classes = _classes()
    for cls in (AuditMiddleware, AuthContextMiddleware, RateLimiterMiddleware, ConcurrencyLimiterMiddleware):
        assert cls in classes


def test_audit_outer_authcontext_outer_ratelimiter():
    classes = _classes()
    assert classes.index(AuditMiddleware) < classes.index(AuthContextMiddleware)
    assert classes.index(AuthContextMiddleware) < classes.index(RateLimiterMiddleware)


def test_concurrency_limiter_is_innermost():
    """It holds a slot for the whole call, so it must sit inside the cheap checks:
    a flood should be rejected by the rate limiter before it can occupy a slot."""
    classes = _classes()
    assert ConcurrencyLimiterMiddleware in classes
    assert classes.index(RateLimiterMiddleware) < classes.index(ConcurrencyLimiterMiddleware)
    assert classes.index(ConcurrencyLimiterMiddleware) == len(classes) - 1
