"""Obtain an API Hub access token through the browser, for SSO/SAML users.

Usage:
    export APIHUB_URL=https://ia-dev.sercomm.com/apihub
    eval "$(python -m scripts.apihub_login --export)"
    python -m scripts.extract_query --db DW --query eo_detail \
        -p frozen_date=2026-08-01 --out eo_detail.csv

``scripts/extract_query.py`` and ``scripts/eo_cache.py`` need APIHUB_API_KEY or
APIHUB_TOKEN, and a person who signs in through isap-auth with SAML has neither:
there is no local password to exchange for a JWT, and an API key belongs to a
service account an administrator has to create. That left SSO users able to ask
questions through MCP but unable to extract in bulk — which is the one job MCP
is the wrong tool for.

The API Hub is an OAuth 2.1 Resource Server (``OAUTH_RS_ENABLED``), so an
isap-auth access token IS an APIHUB_TOKEN. This script runs the same
authorization code + PKCE flow an MCP client runs, against a loopback redirect,
and caches the result. The browser leg is what makes SAML work: the identity
provider is driven by the user in their own browser, exactly as it is for the
Admin UI, and this script never sees a password.

Everything is discovered from APIHUB_URL: its RFC 9728 metadata names the
authorization server, whose RFC 8414 metadata names the endpoints. Nothing here
is specific to isap-auth beyond that.

Exit codes: 0 ok, 1 the flow failed, 2 bad usage.
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx

# Refresh this many seconds before the token actually expires, so a long
# extraction does not start with a credential that dies mid-walk.
EXPIRY_MARGIN_SECONDS = 300
CALLBACK_TIMEOUT_SECONDS = 300
DEFAULT_SCOPE = "openid profile email offline_access"

# Loopback only. A public client cannot keep a secret, so the redirect must be
# somewhere only this machine can receive — never a hostname someone else could
# point at themselves.
REDIRECT_HOST = "127.0.0.1"


def cache_path() -> Path:
    root = os.environ.get("APIHUB_CACHE_DIR") or Path.home() / ".config" / "apihub"
    return Path(root) / "oauth.json"


def _load_cache() -> dict[str, Any]:
    p = cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # a corrupt cache is not worth an error — just log in again


def _save_cache(data: dict[str, Any]) -> None:
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Written before the chmod would matter: create it 0600 rather than
    # narrowing it afterwards, so the refresh token is never briefly readable.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def discover(base_url: str, client: httpx.Client) -> tuple[str, dict[str, Any]]:
    """Return (resource identifier, authorization server metadata)."""
    rs = client.get(f"{base_url.rstrip('/')}/.well-known/oauth-protected-resource")
    rs.raise_for_status()
    rs_meta = rs.json()
    issuers = rs_meta.get("authorization_servers") or []
    if not issuers:
        raise RuntimeError(f"{base_url} names no authorization server in its metadata")
    issuer = issuers[0].rstrip("/")
    # RFC 8414 puts the well-known segment after the host for a path-based
    # issuer; the suffix form is what isap-auth serves and what MCP clients use.
    as_resp = client.get(f"{issuer}/.well-known/oauth-authorization-server")
    as_resp.raise_for_status()
    return rs_meta.get("resource", base_url).rstrip("/"), as_resp.json()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((REDIRECT_HOST, 0))
        return s.getsockname()[1]


def register_client(registration_url: str, redirect_uri: str, client: httpx.Client) -> str:
    resp = client.post(
        registration_url,
        json={
            "client_name": "API Hub bulk extraction (CLI)",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"dynamic client registration failed ({resp.status_code}): {resp.text[:200]}")
    client_id = resp.json().get("client_id")
    if not client_id:
        raise RuntimeError("registration returned no client_id")
    return client_id


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot receiver for the authorization code."""

    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
        query = urllib.parse.urlparse(self.path).query
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        type(self).result = params
        body = (
            "<html><body style='font-family:sans-serif;padding:3em'>"
            + (
                "<h2>Signed in.</h2><p>You can close this tab and return to the terminal.</p>"
                if "code" in params
                else f"<h2>Sign-in failed.</h2><pre>{params.get('error', 'no code returned')}</pre>"
            )
            + "</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args: Any) -> None:
        pass  # the default handler logs every request to stderr


def authorize(
    as_meta: dict[str, Any],
    client_id: str,
    redirect_uri: str,
    resource: str,
    port: int,
    scope: str,
    *,
    open_browser: bool = True,
) -> tuple[str, str]:
    """Drive the browser leg and return (authorization code, PKCE verifier)."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # RFC 8707: bind the token to this API Hub, so it cannot be replayed
        # against another resource server sharing the same issuer.
        "resource": resource,
    }
    url = f"{as_meta['authorization_endpoint']}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer((REDIRECT_HOST, port), _CallbackHandler)
    server.timeout = CALLBACK_TIMEOUT_SECONDS
    _CallbackHandler.result = {}
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Opening your browser to sign in:\n  {url}\n", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    thread.join(CALLBACK_TIMEOUT_SECONDS)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise RuntimeError(f"no callback received within {CALLBACK_TIMEOUT_SECONDS}s")
    if result.get("error"):
        raise RuntimeError(f"authorization failed: {result['error']}")
    # Checked even though the code is useless without the verifier: a mismatch
    # means the callback did not come from the request we started.
    if result.get("state") != state:
        raise RuntimeError("state mismatch — ignoring this callback")
    if not result.get("code"):
        raise RuntimeError("callback carried no authorization code")
    return result["code"], verifier


def exchange_code(
    as_meta: dict[str, Any],
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    resource: str,
    client: httpx.Client,
) -> dict[str, Any]:
    resp = client.post(
        as_meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def refresh(
    as_meta: dict[str, Any], client_id: str, refresh_token: str, resource: str, client: httpx.Client
) -> dict[str, Any] | None:
    """Rotate the refresh token, or None if it is no longer good."""
    resp = client.post(
        as_meta["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": resource,
        },
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def _store(cache: dict[str, Any], key: str, tokens: dict[str, Any], client_id: str, port: int) -> dict[str, Any]:
    previous = cache.get(key, {})
    entry = {
        "client_id": client_id,
        "port": port,
        "access_token": tokens["access_token"],
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 0)),
    }
    # Rotation: isap-auth revokes the whole family if an old refresh token is
    # replayed, so a new one MUST replace the stored one. But a response that
    # carries none is not a revocation — keep what we have, or the next run
    # sends the user back through the browser for nothing.
    entry["refresh_token"] = tokens.get("refresh_token") or previous.get("refresh_token", "")
    if not entry["refresh_token"]:
        del entry["refresh_token"]
    cache[key] = entry
    _save_cache(cache)
    return entry


def get_token(base_url: str, *, force: bool = False, transport: httpx.BaseTransport | None = None) -> str:
    base_url = base_url.rstrip("/")
    cache = _load_cache()
    entry = cache.get(base_url, {})

    if not force and entry.get("access_token") and entry.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS > time.time():
        return entry["access_token"]

    with httpx.Client(timeout=30.0, transport=transport, follow_redirects=False) as client:
        resource, as_meta = discover(base_url, client)

        if not force and entry.get("refresh_token") and entry.get("client_id"):
            tokens = refresh(as_meta, entry["client_id"], entry["refresh_token"], resource, client)
            if tokens:
                return _store(cache, base_url, tokens, entry["client_id"], entry.get("port", 0))["access_token"]
            # Fall through: an expired or revoked refresh token is a reason to
            # sign in again, not to fail.

        port = _free_port()
        redirect_uri = f"http://{REDIRECT_HOST}:{port}/callback"
        registration_url = as_meta.get("registration_endpoint")
        if not registration_url:
            raise RuntimeError(
                "this authorization server does not support dynamic registration — "
                "ask an administrator to register a client and pass --client-id"
            )
        client_id = register_client(registration_url, redirect_uri, client)
        code, verifier = authorize(as_meta, client_id, redirect_uri, resource, port, DEFAULT_SCOPE)
        tokens = exchange_code(as_meta, client_id, code, verifier, redirect_uri, resource, client)
        return _store(cache, base_url, tokens, client_id, port)["access_token"]


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign in to the API Hub through the browser and cache an access token.",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("APIHUB_URL", ""), help="API Hub base URL (env APIHUB_URL)"
    )
    parser.add_argument("--export", action="store_true", help="Print a shell `export` line instead of the bare token")
    parser.add_argument("--force", action="store_true", help="Ignore the cache and sign in again")
    parser.add_argument("--status", action="store_true", help="Report what is cached, without signing in")
    args = parser.parse_args(argv)

    if not args.base_url:
        parser.error("set APIHUB_URL or pass --base-url")

    if args.status:
        entry = _load_cache().get(args.base_url.rstrip("/"))
        if not entry:
            print(f"no token cached for {args.base_url}", file=sys.stderr)
            return 1
        left = int(entry.get("expires_at", 0) - time.time())
        state = f"valid for {left // 60} min" if left > 0 else "EXPIRED"
        print(f"{args.base_url}: {state}, refresh token {'present' if entry.get('refresh_token') else 'absent'}")
        print(f"cache: {cache_path()}", file=sys.stderr)
        return 0

    try:
        token = get_token(args.base_url, force=args.force, transport=transport)
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1

    print(f"export APIHUB_TOKEN={token}" if args.export else token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
