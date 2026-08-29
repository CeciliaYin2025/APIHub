"""One caller → one bucket key, shared by the per-principal middlewares.

The rate limiter and the concurrency limiter must agree on what "one caller"
means, or a caller can be throttled as `user:x` by one and as `ip:10.0.0.1` by
the other. Keeping the rule here means it is defined once.
"""

from fastapi import Request

from app.services.auth_service import get_client_ip


def principal_bucket_key(request: Request) -> str:
    """Return the throttling bucket for this request's resolved principal.

    Requires AuthContextMiddleware to have run (it sets ``request.state.principal``).
    """
    principal = getattr(request, "state", None) and getattr(request.state, "principal", None)

    if principal is not None and principal.type == "api_key":
        return f"api_key:{principal.key_id}"
    if principal is not None:
        # Human callers (password JWT or SSO) get one bucket per username.
        # Keying them on the client IP instead collapses every user behind the
        # reverse proxy into a SINGLE shared bucket, because the direct peer is
        # always the gateway.
        return f"user:{principal.user.username}"
    # Anonymous: still IP-keyed, but through get_client_ip so a configured
    # TRUSTED_PROXIES chain resolves the real client instead of the gateway.
    return f"ip:{get_client_ip(request)}"
