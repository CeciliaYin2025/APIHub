"""Example: read the RD_TC_All query on the DW database as the rd_tc_reader account.

Demonstrates the full client flow against an Oracle API Hub deployment:
  1. POST /auth/token        -> obtain a JWT
  2. POST /{db}/queries/execute with a Bearer token

The rd_tc_reader account holds the permission ``query:RD_TC*`` (read), which —
via prefix-wildcard matching — grants access to ``query:RD_TC_All`` and nothing
else. RD_TC_All lives on the ``DW`` (MSSQL) database and requires two numeric
parameters: ``year`` and ``weekly``.

Run:
    pip install requests
    python examples/read_rd_tc_all.py            # uses defaults below
    YEAR=2025 WEEKLY=1 python examples/read_rd_tc_all.py

Override any setting via environment variables (see CONFIG below). In production,
never hard-code the password — pass it through APIHUB_PASSWORD.
"""

from __future__ import annotations

import os
import sys

import requests

# ── CONFIG (env-overridable) ──────────────────────────────────────────────────
BASE_URL = os.getenv("APIHUB_BASE_URL", "https://ia-dev.sercomm.com/apihub/api/v1")
USERNAME = os.getenv("APIHUB_USERNAME", "rd_tc_reader")
PASSWORD = os.getenv("APIHUB_PASSWORD", "rd_tc_reader2026!")
DB_ALIAS = os.getenv("APIHUB_DB", "DW")
QUERY_NAME = os.getenv("APIHUB_QUERY", "RD_TC_All")
YEAR = int(os.getenv("YEAR", "2025"))
WEEKLY = int(os.getenv("WEEKLY", "1"))

# The ia-dev gateway uses a self-signed/internal certificate. Set
# APIHUB_VERIFY_TLS=1 once a trusted cert is in place.
VERIFY_TLS = os.getenv("APIHUB_VERIFY_TLS", "0") == "1"

if not VERIFY_TLS:
    # Silence the expected "InsecureRequestWarning" for the dev gateway cert.
    requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
        requests.packages.urllib3.exceptions.InsecureRequestWarning  # type: ignore[attr-defined]
    )


def get_token(session: requests.Session) -> str:
    """Authenticate and return a JWT access token."""
    resp = session.post(
        f"{BASE_URL}/auth/token",
        data={"username": USERNAME, "password": PASSWORD},  # OAuth2 form-encoded
        verify=VERIFY_TLS,
        timeout=30,
    )
    if resp.status_code == 401:
        sys.exit(f"[auth] login failed for '{USERNAME}': {resp.json().get('detail')}")
    resp.raise_for_status()
    return resp.json()["access_token"]


def read_rd_tc_all(session: requests.Session, token: str) -> dict:
    """Execute RD_TC_All and return the parsed JSON response."""
    resp = session.post(
        f"{BASE_URL}/{DB_ALIAS}/queries/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query_name": QUERY_NAME,
            "fetch_mode": "page",   # page | all | batch | top_n | one
            "page": 1,
            "page_size": 20,
            "params": {"year": YEAR, "weekly": WEEKLY},
        },
        verify=VERIFY_TLS,
        timeout=60,
    )
    if resp.status_code == 403:
        sys.exit(f"[rbac] permission denied: {resp.json().get('detail')}")
    if resp.status_code == 400:
        sys.exit(f"[params] bad request: {resp.json().get('detail')}")
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    with requests.Session() as session:
        token = get_token(session)
        print(f"[auth] logged in as {USERNAME}")

        result = read_rd_tc_all(session, token)
        columns = result["columns"]
        rows = result["data"]

        print(f"[query] {QUERY_NAME} on '{DB_ALIAS}' "
              f"(year={YEAR}, weekly={WEEKLY}) -> total={result['total']}, "
              f"returned {len(rows)} row(s)")
        print(f"[query] columns: {', '.join(columns)}")
        for i, row in enumerate(rows, 1):
            print(f"  {i:>3}. {row}")


if __name__ == "__main__":
    main()
