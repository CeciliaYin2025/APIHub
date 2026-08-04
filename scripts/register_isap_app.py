"""Self-register this API Hub instance with isap-auth.

Usage:
    python -m scripts.register_isap_app

Run as a compose one-shot before apihub-api starts (see
deploy/apihub/docker-compose.yml). isap-auth's PUT /apps/{app_id} is
declarative and idempotent: it REPLACES redirect_uris wholesale, so
ISAP_REDIRECT_URIS in .env.isap is the single source of truth and re-running
this never leaves stale callbacks behind.

Exit codes: 0 ok or skipped, 1 isap-auth rejected/unreachable, 2 misconfigured.
"""

import sys
from pathlib import Path

import httpx

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings


def parse_redirect_uris(raw: str) -> list[str]:
    """Split the newline- or comma-separated ISAP_REDIRECT_URIS value."""
    parts: list[str] = []
    for line in (raw or "").replace(",", "\n").splitlines():
        uri = line.strip()
        if uri:
            parts.append(uri)
    return parts


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    # No token configured -> this deployment does not use isap-auth SSO. Exit 0
    # so the one-shot does not block apihub-api from starting.
    if not settings.isap_registration_token:
        print("SSO registration skipped (no ISAP_REGISTRATION_TOKEN configured).")
        return 0

    redirect_uris = parse_redirect_uris(settings.isap_redirect_uris)
    missing = [
        name
        for name, value in (
            ("ISAP_APP_ID", settings.isap_app_id),
            ("ISAP_AUTH_INTERNAL_URL", settings.isap_auth_internal_url),
            ("ISAP_REDIRECT_URIS", "\n".join(redirect_uris)),
        )
        if not value
    ]
    if missing:
        print(
            f"ISAP_REGISTRATION_TOKEN is set but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} empty; cannot register.",
            file=sys.stderr,
        )
        return 2

    url = f"{settings.isap_auth_internal_url.rstrip('/')}/api/v1/apps/{settings.isap_app_id}"
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            resp = client.put(
                url,
                json={"app_name": settings.isap_app_name, "redirect_uris": redirect_uris},
                headers={"Authorization": f"Bearer {settings.isap_registration_token}"},
            )
    except httpx.HTTPError as e:
        print(
            f"Cannot reach isap-auth at {url}: {e}\n"
            "isap-auth must be running and on the same docker network (gateway-net).",
            file=sys.stderr,
        )
        return 1

    try:
        body = resp.json() or {}
    except ValueError:
        body = {}
    detail = body.get("msg") or resp.text

    if resp.status_code == 401:
        print(
            f"isap-auth rejected the bootstrap token (401): {detail}\n"
            "Mint a new one: docker exec isap-auth python -m app.cli rotate-token "
            f"{settings.isap_app_id}",
            file=sys.stderr,
        )
        return 1
    if resp.status_code == 403:
        print(
            f"Bootstrap token is not valid for app_id {settings.isap_app_id!r} (403): {detail}\n"
            "The token is scoped to exactly one app_id — check ISAP_APP_ID against "
            "the app_id the token was issued for.",
            file=sys.stderr,
        )
        return 1
    if resp.status_code != 200:
        print(f"isap-auth rejected the registration ({resp.status_code}): {detail}", file=sys.stderr)
        return 1

    data = body.get("data") or {}
    print(
        f"Registered {data.get('app_id', settings.isap_app_id)!r} "
        f"(name={data.get('app_name')!r}, is_active={data.get('is_active')})"
    )
    for uri in data.get("redirect_uris") or redirect_uris:
        print(f"  redirect_uri: {uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
