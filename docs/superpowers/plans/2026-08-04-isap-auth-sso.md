# isap-auth 自註冊與 Admin UI SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Oracle API Hub 在啟動時自動向 isap-auth 註冊自己，並讓 Admin UI 支援 ISAP SSO 登入；同時補上 MCP OAuth 所需的 `OAUTH_DCR_ENABLED` 前置條件文件。

**Architecture:** 三條工作線。註冊走平台契約的 compose one-shot（`scripts/register_isap_app.py` → `PUT /apps/{app_id}`）。Admin UI SSO 走 isap-auth 的方法 1（傳統 app-code）：`/authorize` → `POST /token` 取 user info → API Hub 自己 mint JWT → 以 URL fragment 交回 SPA。使用者解析邏輯從既有的 MCP identity bridge 抽成 `app/services/sso_service.py`，兩條入口共用。

**Tech Stack:** FastAPI、SQLAlchemy、httpx（已在 `requirements.txt`）、原生 JS SPA、Docker Compose、pytest、Playwright。

設計文件：`docs/superpowers/specs/2026-08-04-isap-auth-sso-design.md`

## Global Constraints

- **設定一律經 Pydantic Settings**（`app/core/config.py`），不直接讀 `os.environ`；每個新設定都要有預設值。
- **`ADMIN_SSO_ENABLED` 預設 `false`**，關閉時兩個 SSO 端點回 404、登入頁不顯示按鈕。現有部署（本機 uvicorn、根目錄 compose）行為必須完全不變。
- **`SSO_AUTOCREATE` 預設 `false`**；`MCP_BRIDGE_AUTOCREATE` 保留為相容別名，兩者為 OR 關係。
- **`pytest` 沒有 live Postgres**：DB 相關測試用 `_FakeDB` 直接呼叫 router 函式（見 `tests/test_api_key_endpoints.py`），外部 HTTP 用 `httpx.MockTransport`（見 `tests/mcp/test_oauth.py`）。
- **`pytest -v` 不會跑 `tests/e2e`**（`norecursedirs` 排除），e2e 需另外跑。
- **bootstrap token 不進 `.env`**：`ISAP_REGISTRATION_TOKEN`、`ISAP_REDIRECT_URIS`、`ISAP_APP_NAME` 只放 `.env.isap`，僅註冊 job 讀取。
- **`APIHUB_PUBLIC_URL` 必須與 `ISAP_REDIRECT_URIS` 登記的位址一致**，否則 `/authorize` 回 `Unknown app_id or redirect_uri not allowed`。
- **isap-auth 路徑常數**：`{base}/api/v1/authorize`、`{base}/api/v1/token`、`{base}/api/v1/apps/{app_id}`。回應包在 `{"code","msg","data"}` 信封中。
- **每個 task 結束都要 commit**；commit message 用 `feat:` / `test:` / `docs:` / `refactor:` 前綴，結尾加 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。

---

## File Structure

| 檔案 | 職責 |
|---|---|
| `app/core/config.py` | 新增 8 個設定欄位（modify） |
| `app/services/sso_service.py` | **新增**。SSO 身分解析 + auth code 交換，兩條入口共用 |
| `app/api/v1/auth.py` | 新增 `/sso/login`、`/sso/callback`；`sso_token_bridge` 改用 service（modify） |
| `scripts/register_isap_app.py` | **新增**。註冊 one-shot 的進入點 |
| `app/admin/index.html` | 登入卡片加 SSO 按鈕（modify） |
| `app/admin/js/app.js` | 開機處理 fragment（modify） |
| `app/main.py` | 注入 `__SSO_ENABLED__`（modify） |
| `deploy/apihub{,-test}/docker-compose.yml` | 新增 `*-app-register` one-shot（modify） |
| `deploy/apihub{,-test}/.env.isap.example` | **新增** |
| `tests/test_sso_service.py`、`test_sso_endpoints.py`、`test_register_isap_app.py` | **新增** |
| `tests/e2e/sso-login.spec.js` | **新增** |

---

## Task 1: 設定欄位與共用 SSO service

**Files:**
- Modify: `app/core/config.py:74-78`（MCP bridge 區塊之後）
- Create: `app/services/sso_service.py`
- Modify: `app/api/v1/auth.py:330-367`（`sso_token_bridge`）
- Test: `tests/test_sso_service.py`

**Interfaces:**
- Consumes: `app.models.user.User`、`app.core.security.get_password_hash`
- Produces:
  - `sso_service.autocreate_enabled() -> bool`
  - `sso_service.resolve_sso_user(db, username: str, email: str = "") -> User`（拒絕時 `HTTPException(403)`）
  - `sso_service.SsoExchangeError(reason: str)`，`reason` ∈ `{"auth_unreachable", "invalid_code"}`
  - `sso_service.exchange_sso_code(code: str, *, transport=None) -> dict`（回傳 user info dict）

- [ ] **Step 1: 新增設定欄位**

在 `app/core/config.py` 的 `mcp_bridge_autocreate: bool = False` 之後、`# SQL definitions` 之前插入：

```python
    # ── isap-auth SSO ────────────────────────────────────────────────────────
    # Admin UI SSO login via isap-auth (legacy app-code flow). Off by default so
    # deployments without isap-auth (bare uvicorn, root docker-compose) are
    # unaffected: both /auth/sso/* endpoints 404 and the UI hides the button.
    admin_sso_enabled: bool = False
    # This service's own public address, e.g. https://isap.sercomm.com/apihub.
    # The redirect_uri handed to isap-auth must be ABSOLUTE and must match a line
    # in ISAP_REDIRECT_URIS exactly — root_path alone ("/apihub") cannot build it.
    apihub_public_url: str = ""
    # isap-auth base URLs. Public = browser-facing /authorize; internal = the
    # docker-network POST /token, which never leaves the gateway network.
    isap_auth_public_url: str = ""
    isap_auth_internal_url: str = ""
    isap_app_id: str = "apihub"
    # Auto-create a role-less local user on first SSO login. Applies to BOTH the
    # Admin UI callback and the MCP identity bridge; mcp_bridge_autocreate stays
    # as a backward-compatible alias (see sso_service.autocreate_enabled).
    sso_autocreate: bool = False

    # Read only by scripts/register_isap_app.py — the api container is not given
    # these, so the bootstrap token never sits in a long-running process.
    isap_app_name: str = "Oracle API Hub"
    isap_registration_token: str = ""
    isap_redirect_uris: str = ""  # newline- or comma-separated
```

- [ ] **Step 2: 寫失敗的測試**

建立 `tests/test_sso_service.py`：

```python
"""Shared SSO identity resolution used by both the MCP bridge and Admin UI."""

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.user import User
from app.services.sso_service import (
    SsoExchangeError,
    autocreate_enabled,
    exchange_sso_code,
    resolve_sso_user,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None):
        self._user = user
        self.added = []

    def query(self, model):
        return _FakeQuery(self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)


def test_existing_user_is_returned_without_insert():
    user = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    db = _FakeDB(user=user)
    assert resolve_sso_user(db, "alice") is user
    assert db.added == []


def test_unknown_user_rejected_when_autocreate_off():
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=None), "bob")
    assert exc.value.status_code == 403
    assert "provisioned" in exc.value.detail


def test_unknown_user_created_when_autocreate_on(monkeypatch):
    monkeypatch.setattr(settings, "sso_autocreate", True)
    db = _FakeDB(user=None)
    user = resolve_sso_user(db, "carol")
    assert user.username == "carol"
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.is_service_account is False
    # SSO users have no usable local password: the hash must not be empty and
    # must not be a hash of the empty string.
    assert user.hashed_password
    assert db.added == [user]


def test_mcp_bridge_autocreate_still_works_as_alias(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", True)
    assert autocreate_enabled() is True
    db = _FakeDB(user=None)
    assert resolve_sso_user(db, "dave").username == "dave"


def test_service_account_rejected():
    user = User(username="svc", is_active=True, is_superuser=False, is_service_account=True)
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=user), "svc")
    assert exc.value.status_code == 403


def test_inactive_user_rejected():
    user = User(username="gone", is_active=False, is_superuser=False, is_service_account=False)
    with pytest.raises(HTTPException) as exc:
        resolve_sso_user(_FakeDB(user=user), "gone")
    assert exc.value.status_code == 403


def _transport(handler):
    return httpx.MockTransport(handler)


def test_exchange_returns_user_info(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"code": 200, "msg": "OK", "data": {"username": "alice", "email": "a@x"}})

    info = exchange_sso_code("the-code", transport=_transport(handler))
    assert info["username"] == "alice"
    assert seen["url"] == "http://isap-auth:9997/auth/api/v1/token"
    assert '"code": "the-code"' in seen["json"] or '"code":"the-code"' in seen["json"]
    assert "apihub" in seen["json"]


def test_exchange_maps_connection_failure(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "auth_unreachable"


def test_exchange_maps_rejected_code(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        return httpx.Response(401, json={"code": 401, "msg": "Invalid, expired, or already used auth code"})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"


def test_exchange_maps_missing_username(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")

    def handler(request):
        return httpx.Response(200, json={"code": 200, "msg": "OK", "data": {}})

    with pytest.raises(SsoExchangeError) as exc:
        exchange_sso_code("x", transport=_transport(handler))
    assert exc.value.reason == "invalid_code"
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `python -m pytest tests/test_sso_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.sso_service'`

- [ ] **Step 4: 建立 `app/services/sso_service.py`**

```python
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
```

- [ ] **Step 5: 執行測試確認通過**

Run: `python -m pytest tests/test_sso_service.py -v`
Expected: PASS（11 個測試）

- [ ] **Step 6: 讓 `sso_token_bridge` 改用 service**

`app/api/v1/auth.py` 匯入區加入：

```python
from app.services.sso_service import resolve_sso_user
```

把 `sso_token_bridge` 中從 `user = db.query(User).filter(...)` 到 `raise HTTPException(status_code=403, detail="Account not eligible for SSO bridge")` 的整段（現行 `auth.py:347-364`）替換為：

```python
    user = resolve_sso_user(db, payload.username, payload.email or "")
```

`secrets` 與 `get_password_hash` 若在該檔已無其他用途則保留不動（`secrets.compare_digest` 仍在用，`get_password_hash` 被密碼重設端點使用）——不要刪除 import。

- [ ] **Step 7: 跑既有的 bridge 回歸測試**

Run: `python -m pytest tests/ -k "sso or bridge" -v`
Expected: PASS，行為與重構前一致

- [ ] **Step 8: 跑完整測試套件**

Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 9: Commit**

```bash
git add app/core/config.py app/services/sso_service.py app/api/v1/auth.py tests/test_sso_service.py
git commit -m "feat(sso): extract shared SSO user resolution and code exchange

Adds app/services/sso_service.py so the MCP identity bridge and the
upcoming Admin UI callback share one provisioning policy, plus the
isap-auth settings both need. SSO_AUTOCREATE supersedes
MCP_BRIDGE_AUTOCREATE, which stays as an alias.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 註冊腳本 `scripts/register_isap_app.py`

**Files:**
- Create: `scripts/register_isap_app.py`
- Test: `tests/test_register_isap_app.py`

**Interfaces:**
- Consumes: `app.core.config.settings`（Task 1 新增的 `isap_*` 欄位）
- Produces: `register_isap_app.parse_redirect_uris(raw: str) -> list[str]`、`register_isap_app.main(argv=None, *, transport=None) -> int`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_register_isap_app.py`：

```python
"""Self-registration one-shot: PUT /apps/{app_id} against isap-auth."""

import httpx
import pytest

from app.core.config import settings
from scripts.register_isap_app import main, parse_redirect_uris


@pytest.fixture(autouse=True)
def _isap_settings(monkeypatch):
    monkeypatch.setattr(settings, "isap_auth_internal_url", "http://isap-auth:9997/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")
    monkeypatch.setattr(settings, "isap_app_name", "Oracle API Hub")
    monkeypatch.setattr(settings, "isap_registration_token", "isapreg_test")
    monkeypatch.setattr(settings, "isap_redirect_uris", "https://h/apihub/api/v1/auth/sso/callback")


def test_parse_redirect_uris_handles_newlines_and_commas():
    assert parse_redirect_uris("https://a\nhttps://b") == ["https://a", "https://b"]
    assert parse_redirect_uris("https://a, https://b") == ["https://a", "https://b"]
    assert parse_redirect_uris("  \n ") == []


def test_no_token_skips_without_any_request(monkeypatch):
    monkeypatch.setattr(settings, "isap_registration_token", "")
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, json={})

    assert main(transport=httpx.MockTransport(handler)) == 0
    assert called == []


@pytest.mark.parametrize("field", ["isap_app_id", "isap_auth_internal_url", "isap_redirect_uris"])
def test_missing_config_exits_2(monkeypatch, field):
    monkeypatch.setattr(settings, field, "")
    assert main(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) == 2


def test_successful_registration(capsys):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "code": 200,
                "msg": "OK",
                "data": {
                    "app_id": "apihub",
                    "app_name": "Oracle API Hub",
                    "redirect_uris": ["https://h/apihub/api/v1/auth/sso/callback"],
                    "is_active": True,
                },
            },
        )

    assert main(transport=httpx.MockTransport(handler)) == 0
    assert seen["method"] == "PUT"
    assert seen["url"] == "http://isap-auth:9997/auth/api/v1/apps/apihub"
    assert seen["auth"] == "Bearer isapreg_test"
    assert "sso/callback" in seen["body"]
    assert "apihub" in capsys.readouterr().out


def test_connection_failure_exits_1():
    def handler(request):
        raise httpx.ConnectError("down")

    assert main(transport=httpx.MockTransport(handler)) == 1


@pytest.mark.parametrize("status", [400, 401, 403])
def test_error_statuses_exit_1(status, capsys):
    def handler(request):
        return httpx.Response(status, json={"code": status, "msg": "denied"})

    assert main(transport=httpx.MockTransport(handler)) == 1
    assert "denied" in capsys.readouterr().err
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_register_isap_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.register_isap_app'`

- [ ] **Step 3: 建立 `scripts/register_isap_app.py`**

```python
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_register_isap_app.py -v`
Expected: PASS（11 個測試，含 parametrize 展開）

- [ ] **Step 5: 手動確認跳過路徑**

Run: `python -m scripts.register_isap_app`
Expected: 印出 `SSO registration skipped (no ISAP_REGISTRATION_TOKEN configured).`，退出碼 0（本機 `.env` 沒有該變數）

- [ ] **Step 6: Commit**

```bash
git add scripts/register_isap_app.py tests/test_register_isap_app.py
git commit -m "feat(sso): add isap-auth self-registration one-shot script

PUT /apps/{app_id} with the per-app bootstrap token. Declarative and
idempotent; exits 0 when no token is configured so deployments that do
not use isap-auth are unaffected.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: SSO 登入與 callback 端點

**Files:**
- Modify: `app/api/v1/auth.py`（匯入區 + 檔案末尾新增區塊）
- Test: `tests/test_sso_endpoints.py`

**Interfaces:**
- Consumes: `sso_service.resolve_sso_user`、`sso_service.exchange_sso_code`、`sso_service.SsoExchangeError`（Task 1）
- Produces: `auth.sso_login()`、`auth.sso_callback(request, code, state, db)`、常數 `auth.SSO_STATE_COOKIE = "apihub_sso_state"`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_sso_endpoints.py`：

```python
"""Admin UI SSO endpoints: /auth/sso/login and /auth/sso/callback."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.api.v1 import auth as auth_module
from app.api.v1.auth import SSO_STATE_COOKIE, sso_callback, sso_login
from app.core.config import settings
from app.models.user import User
from app.services.sso_service import SsoExchangeError


class _FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None):
        self._user = user

    def query(self, model):
        return _FakeQuery(self._user)


@pytest.fixture(autouse=True)
def _sso_on(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", True)
    monkeypatch.setattr(settings, "apihub_public_url", "https://h/apihub")
    monkeypatch.setattr(settings, "isap_auth_public_url", "https://h/auth")
    monkeypatch.setattr(settings, "isap_app_id", "apihub")
    monkeypatch.setattr(settings, "root_path", "/apihub")
    monkeypatch.setattr(settings, "api_prefix", "/api/v1")


def _fragment(response):
    return urlparse(response.headers["location"]).fragment


def test_login_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", False)
    with pytest.raises(HTTPException) as exc:
        sso_login()
    assert exc.value.status_code == 404


def test_callback_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_sso_enabled", False)
    with pytest.raises(HTTPException) as exc:
        sso_callback(_FakeRequest(), code="c", state="s", db=_FakeDB())
    assert exc.value.status_code == 404


def test_login_redirects_to_authorize_with_state():
    resp = sso_login()
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/auth/api/v1/authorize"
    qs = parse_qs(parsed.query)
    assert qs["app_id"] == ["apihub"]
    assert qs["redirect_uri"] == ["https://h/apihub/api/v1/auth/sso/callback"]
    assert qs["state"][0]


def test_login_sets_httponly_samesite_cookie():
    resp = sso_login()
    cookie = resp.headers["set-cookie"]
    assert SSO_STATE_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("samesite", "SameSite")


def test_login_state_matches_cookie():
    resp = sso_login()
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    assert state in resp.headers["set-cookie"]


def test_callback_state_mismatch():
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "aaa"}), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_cookie():
    resp = sso_callback(_FakeRequest(), code="c", state="bbb", db=_FakeDB())
    assert _fragment(resp) == "sso_error=state_mismatch"


def test_callback_missing_code():
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=missing_code"


def test_callback_auth_unreachable(monkeypatch):
    def boom(code, **kw):
        raise SsoExchangeError("auth_unreachable")

    monkeypatch.setattr(auth_module, "exchange_sso_code", boom)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=auth_unreachable"


def test_callback_invalid_code(monkeypatch):
    def boom(code, **kw):
        raise SsoExchangeError("invalid_code")

    monkeypatch.setattr(auth_module, "exchange_sso_code", boom)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB())
    assert _fragment(resp) == "sso_error=invalid_code"


def test_callback_not_provisioned(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "bob"})
    monkeypatch.setattr(settings, "sso_autocreate", False)
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=None))
    assert _fragment(resp) == "sso_error=not_provisioned"


def test_callback_not_eligible(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "svc"})
    svc = User(username="svc", is_active=True, is_superuser=False, is_service_account=True)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=svc))
    assert _fragment(resp) == "sso_error=not_eligible"


def test_callback_success_returns_token_fragment(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "alice"})
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=alice))
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/apihub/admin"
    assert parsed.fragment.startswith("sso_token=")

    from jose import jwt

    raw = parsed.fragment.split("=", 1)[1]
    claims = jwt.decode(raw, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"


def test_callback_clears_state_cookie(monkeypatch):
    monkeypatch.setattr(auth_module, "exchange_sso_code", lambda code, **kw: {"username": "alice"})
    alice = User(username="alice", is_active=True, is_superuser=False, is_service_account=False)
    resp = sso_callback(_FakeRequest({SSO_STATE_COOKIE: "s"}), code="c", state="s", db=_FakeDB(user=alice))
    assert SSO_STATE_COOKIE in resp.headers["set-cookie"]
    assert 'Max-Age=0' in resp.headers["set-cookie"] or 'expires=' in resp.headers["set-cookie"].lower()
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_sso_endpoints.py -v`
Expected: FAIL — `ImportError: cannot import name 'SSO_STATE_COOKIE'`

- [ ] **Step 3: 擴充 `app/api/v1/auth.py` 匯入區**

在既有 import 中加入（合併進同名的既有 import 行，不要重複匯入）：

```python
from urllib.parse import urlencode

from fastapi import Query, Request
from fastapi.responses import RedirectResponse

from app.services.sso_service import SsoExchangeError, exchange_sso_code, resolve_sso_user
```

- [ ] **Step 4: 在 `app/api/v1/auth.py` 末尾新增端點**

接在 `sso_token_bridge` 之後：

```python
# ── Admin UI SSO (isap-auth legacy app-code flow) ───────────────────────────

SSO_STATE_COOKIE = "apihub_sso_state"


def _require_sso_enabled() -> None:
    """404 rather than 403 when SSO is off — the route should not exist."""
    if not settings.admin_sso_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _sso_callback_url() -> str:
    """Absolute callback URL; must match a line in ISAP_REDIRECT_URIS exactly."""
    return f"{settings.apihub_public_url.rstrip('/')}{settings.api_prefix}/auth/sso/callback"


def _admin_redirect(fragment: str) -> RedirectResponse:
    """Hand the result back to the SPA in the URL fragment.

    A fragment is never sent to the server, so the JWT stays out of nginx
    access logs and Referer headers. The SPA strips it via history.replaceState
    as soon as it has read it.
    """
    response = RedirectResponse(url=f"{settings.root_path}/admin#{fragment}", status_code=302)
    response.delete_cookie(SSO_STATE_COOKIE, path="/")
    return response


@router.get("/sso/login", include_in_schema=False)
def sso_login():
    """Start the SSO flow: bounce the browser to isap-auth's /authorize."""
    _require_sso_enabled()
    state = secrets.token_urlsafe(32)
    params = urlencode(
        {
            "app_id": settings.isap_app_id,
            "redirect_uri": _sso_callback_url(),
            "state": state,
        }
    )
    response = RedirectResponse(
        url=f"{settings.isap_auth_public_url.rstrip('/')}/api/v1/authorize?{params}",
        status_code=302,
    )
    # Login-CSRF guard: without it an attacker can feed a victim a callback URL
    # carrying the attacker's own code and silently log them into the attacker's
    # account. isap-auth echoes `state` back unchanged, so we can compare.
    response.set_cookie(
        SSO_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
    )
    return response


@router.get("/sso/callback", include_in_schema=False)
def sso_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Exchange the auth code, map to a local user, hand a JWT to the SPA.

    Every failure path redirects with a `#sso_error=` fragment rather than
    returning JSON — this endpoint is reached by a browser navigation, so the
    Admin UI login page is the only sensible place to surface an error.
    """
    _require_sso_enabled()

    expected = request.cookies.get(SSO_STATE_COOKIE, "")
    if not expected or not state or not secrets.compare_digest(state, expected):
        return _admin_redirect("sso_error=state_mismatch")
    if not code:
        return _admin_redirect("sso_error=missing_code")

    try:
        info = exchange_sso_code(code)
    except SsoExchangeError as e:
        return _admin_redirect(f"sso_error={e.reason}")

    try:
        user = resolve_sso_user(db, info["username"], info.get("email") or "")
    except HTTPException as e:
        reason = "not_provisioned" if "provisioned" in str(e.detail) else "not_eligible"
        return _admin_redirect(f"sso_error={reason}")

    access_token = create_access_token(data={"sub": user.username})
    return _admin_redirect(f"sso_token={access_token}")
```

- [ ] **Step 5: 執行測試確認通過**

Run: `python -m pytest tests/test_sso_endpoints.py -v`
Expected: PASS（14 個測試）

- [ ] **Step 6: 確認 SSO 關閉時路由不出現在 OpenAPI**

Run: `python -c "from app.main import app; print([r.path for r in app.routes if 'sso' in r.path])"`
Expected: 印出兩條 `/api/v1/auth/sso/...` 路徑（路由永遠註冊，由 `_require_sso_enabled` 在執行期 404），且因 `include_in_schema=False` 不進 OpenAPI

- [ ] **Step 7: 跑完整測試套件**

Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/auth.py tests/test_sso_endpoints.py
git commit -m "feat(sso): add Admin UI SSO login and callback endpoints

/auth/sso/login bounces to isap-auth with a state cookie; /auth/sso/callback
exchanges the code, resolves the local user and returns the JWT in a URL
fragment. State comparison guards against login CSRF. Both 404 when
ADMIN_SSO_ENABLED is off.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Admin UI SSO 按鈕與 fragment 處理

**Files:**
- Modify: `app/main.py:84-88`（`admin_spa`）
- Modify: `app/admin/index.html:15-32`（登入卡片）、`:121`（設定注入）
- Modify: `app/admin/js/app.js:17-27`（bootstrap）
- Test: `tests/test_admin_sso_flag.py`、`tests/e2e/sso-login.spec.js`

**Interfaces:**
- Consumes: Task 3 的 `#sso_token=` / `#sso_error=` 契約
- Produces: `window.__SSO_ENABLED__`（boolean）、`{{SSO_ENABLED}}` 樣板變數

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_admin_sso_flag.py`：

```python
"""The Admin SPA template must learn whether SSO is available."""

from app.api.v1 import auth  # noqa: F401  (ensures app imports cleanly)
from app.core.config import settings
from app.main import admin_spa


def _html(monkeypatch, enabled: bool) -> str:
    monkeypatch.setattr(settings, "admin_sso_enabled", enabled)
    return admin_spa().body.decode()


def test_sso_flag_true_is_injected(monkeypatch):
    html = _html(monkeypatch, True)
    assert "window.__SSO_ENABLED__ = true" in html
    assert "{{SSO_ENABLED}}" not in html


def test_sso_flag_false_is_injected(monkeypatch):
    html = _html(monkeypatch, False)
    assert "window.__SSO_ENABLED__ = false" in html
    assert "{{SSO_ENABLED}}" not in html


def test_base_path_still_substituted(monkeypatch):
    monkeypatch.setattr(settings, "root_path", "/apihub")
    html = _html(monkeypatch, False)
    assert "{{BASE_PATH}}" not in html
    assert "/apihub/admin/css/style.css" in html
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_admin_sso_flag.py -v`
Expected: FAIL — `assert "window.__SSO_ENABLED__ = true" in html`

- [ ] **Step 3: 在 `app/main.py` 注入旗標**

把 `admin_spa` 的 body 改為：

```python
    html = (_ADMIN_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{BASE_PATH}}", settings.root_path)
    # Lower-cased so it is a valid JS boolean literal, not Python's True/False.
    html = html.replace("{{SSO_ENABLED}}", "true" if settings.admin_sso_enabled else "false")
    return HTMLResponse(content=html)
```

- [ ] **Step 4: 在 `app/admin/index.html` 注入旗標並加按鈕**

把 `:121` 的設定行改為：

```html
<script>window.__BASE_PATH__ = "{{BASE_PATH}}"; window.__SSO_ENABLED__ = {{SSO_ENABLED}};</script>
```

在登入卡片中，`<p class="subtitle">Management Console</p>` 之後、`<form id="loginForm">` 之前插入：

```html
    <div id="ssoBlock" class="hidden">
      <a id="ssoLoginBtn" class="btn btn-primary" style="width:100%;justify-content:center;padding:10px;text-decoration:none;">Sign in with ISAP SSO</a>
      <div class="sso-divider"><span>or</span></div>
    </div>
```

- [ ] **Step 5: 加上分隔線樣式**

在 `app/admin/css/style.css` 末尾新增：

```css
/* ── SSO login divider ──────────────────────────────────────────────────── */
.sso-divider {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 0;
  color: var(--text-muted, #888);
  font-size: 12px;
}
.sso-divider::before,
.sso-divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border, #ddd);
}
```

- [ ] **Step 6: 在 `app/admin/js/app.js` 處理 fragment**

把 `DOMContentLoaded` 的 handler（`app.js:17-27`）替換為：

```javascript
/* Fragment slugs produced by GET /auth/sso/callback — keep in sync with
   app/services/sso_service.py and app/api/v1/auth.py. */
const SSO_ERRORS = {
  missing_code: 'SSO login failed: no authorization code was returned.',
  state_mismatch: 'SSO login failed: the login attempt could not be verified. Please try again.',
  auth_unreachable: 'SSO login failed: the ISAP authentication service is unreachable.',
  invalid_code: 'SSO login failed: the authorization code was invalid or had expired.',
  not_provisioned: 'Your SSO account has no API Hub user yet. Ask an administrator to create one.',
  not_eligible: 'This account may not sign in via SSO.',
};

/**
 * Consume a #sso_token= / #sso_error= fragment left by the SSO callback.
 * The fragment is stripped immediately so the JWT does not linger in the
 * address bar or get re-applied on reload.
 */
function consumeSsoFragment() {
  const hash = window.location.hash || '';
  if (!hash.startsWith('#sso_')) return null;
  const [key, ...rest] = hash.slice(1).split('=');
  const value = rest.join('=');
  history.replaceState(null, '', window.location.pathname + window.location.search);
  if (key === 'sso_token' && value) return { token: value };
  if (key === 'sso_error') return { error: SSO_ERRORS[value] || 'SSO login failed.' };
  return null;
}

document.addEventListener('DOMContentLoaded', () => {
  if (window.__SSO_ENABLED__) {
    const block = document.getElementById('ssoBlock');
    const btn = document.getElementById('ssoLoginBtn');
    if (block) block.classList.remove('hidden');
    if (btn) btn.href = `${API}/auth/sso/login`;
  }

  const sso = consumeSsoFragment();
  if (sso && sso.error) {
    showLogin();
    toast(sso.error, 'error');
  } else if (sso && sso.token) {
    token = sso.token;
    localStorage.setItem('hub_token', token);
  }

  if (token) {
    fetchCurrentUser().then(ok => {
      if (ok) showApp();
      else showLogin();
    });
  } else if (!(sso && sso.error)) {
    showLogin();
  }
  bindGlobalEvents();
});
```

- [ ] **Step 7: 執行測試確認通過**

Run: `python -m pytest tests/test_admin_sso_flag.py -v`
Expected: PASS（3 個測試）

- [ ] **Step 8: 寫 e2e 測試**

建立 `tests/e2e/sso-login.spec.js`。先讀 `tests/e2e/` 中任一既有 spec（例如 `usage-report.spec.js`）確認 fixture / helper 命名，沿用同樣的 `test.describe` 與登入 helper 慣例，內容涵蓋：

```javascript
// tests/e2e/sso-login.spec.js
//
// Admin UI SSO surface. Does NOT need a live isap-auth: the callback contract
// is just a URL fragment, so we drive the SPA by navigating to one directly.
const { test, expect } = require('@playwright/test');

test.describe('Admin UI SSO', () => {
  test('a valid #sso_token= fragment signs the user in and is stripped', async ({ page, baseURL }) => {
    // Mint a token the same way GET /auth/sso/callback would, via the normal
    // password login, then replay it through the SSO fragment path.
    const res = await page.request.post(`${baseURL}/api/v1/auth/token`, {
      form: { username: 'admin', password: process.env.ADMIN_PASSWORD || 'admin123' },
    });
    const { access_token: jwt } = await res.json();

    await page.goto(`${baseURL}/admin#sso_token=${jwt}`);
    await expect(page.locator('#appLayout')).toBeVisible();
    expect(page.url()).not.toContain('#');
    expect(await page.evaluate(() => localStorage.getItem('hub_token'))).toBe(jwt);
  });

  test('an #sso_error= fragment stays on the login page with a message', async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/admin#sso_error=not_provisioned`);
    await expect(page.locator('#loginPage')).toBeVisible();
    await expect(page.locator('.toast-container')).toContainText('no API Hub user');
    expect(page.url()).not.toContain('#');
  });

  test('the SSO button is hidden when ADMIN_SSO_ENABLED is off', async ({ page, baseURL }) => {
    // The e2e stack runs with SSO disabled (no isap-auth in the compose file).
    await page.goto(`${baseURL}/admin`);
    await expect(page.locator('#ssoBlock')).toBeHidden();
  });
});
```

- [ ] **Step 9: 跑 e2e**

Run: `cd tests/e2e && npm test -- sso-login.spec.js`
Expected: 3 個測試 PASS。若 helper 命名與既有 spec 不同，依既有 spec 修正後再跑。

- [ ] **Step 10: Commit**

```bash
git add app/main.py app/admin/index.html app/admin/css/style.css app/admin/js/app.js tests/test_admin_sso_flag.py tests/e2e/sso-login.spec.js
git commit -m "feat(admin-ui): add ISAP SSO sign-in button and fragment handling

The login page shows an SSO button when ADMIN_SSO_ENABLED is on, and the
SPA consumes the #sso_token= / #sso_error= fragment the callback leaves,
stripping it via history.replaceState.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 部署接線（正式 + 測試兩套）

**Files:**
- Modify: `deploy/apihub/docker-compose.yml`（新增 `apihub-app-register`、`apihub-api` 加 `depends_on` 與 `environment`）
- Modify: `deploy/apihub-test/docker-compose.yml`（同構，`apihub-test-` 前綴）
- Create: `deploy/apihub/.env.isap.example`、`deploy/apihub-test/.env.isap.example`
- Modify: `deploy/apihub/.env.example`、`deploy/apihub-test/.env.example`

**Interfaces:**
- Consumes: `scripts/register_isap_app.py`（Task 2）、Task 1 的設定名稱

- [ ] **Step 1: 建立 `deploy/apihub/.env.isap.example`**

```bash
# ==============================================================================
# Oracle API Hub — isap-auth self-registration (apihub-app-register) — PROD
#   cp .env.isap.example .env.isap  &&  edit every <...> value
#   grep -nE '<[^>]+>' .env.isap    # must return nothing
# ==============================================================================
# Consumed ONLY by the apihub-app-register one-shot. The bootstrap token is
# deliberately kept out of .env: it can rewrite this app's redirect_uris, so a
# leak is an auth-code theft vector, and .env is shared by apihub-api and every
# celery worker.
#
# Mint the token on the isap-auth host:
#   docker exec isap-auth python -m app.cli issue-token apihub --name "Oracle API Hub"
ISAP_REGISTRATION_TOKEN=<isapreg_... from the command above>

ISAP_APP_NAME=Oracle API Hub

# Declarative: PUT /apps/{app_id} REPLACES the registered list wholesale, so this
# is the single source of truth. Newline- or comma-separated.
# MUST match APIHUB_PUBLIC_URL in .env + /api/v1/auth/sso/callback exactly.
# isap-auth accepts https:// anywhere; http:// only on loopback hosts.
ISAP_REDIRECT_URIS=https://<GATEWAY_SERVER_NAME>/apihub/api/v1/auth/sso/callback
```

- [ ] **Step 2: 在 `deploy/apihub/.env.example` 新增 SSO 區塊**

接在 `MCP_BRIDGE_AUTOCREATE=false` 之後插入：

```bash
# --- isap-auth SSO (apihub-api side) ------------------------------------------
# Admin UI SSO login via isap-auth. The registration one-shot has its own env
# file (.env.isap / .env.isap.example); only the values below belong to
# apihub-api. Leave ADMIN_SSO_ENABLED=false to keep the feature entirely off —
# both /api/v1/auth/sso/* routes then return 404 and the login page hides the
# SSO button.
ADMIN_SSO_ENABLED=true
# This service's public address. The redirect_uri handed to isap-auth is built
# from it and MUST match a line in .env.isap's ISAP_REDIRECT_URIS exactly,
# otherwise /authorize answers "Unknown app_id or redirect_uri not allowed".
APIHUB_PUBLIC_URL=https://<GATEWAY_SERVER_NAME>/apihub
# Browser-facing issuer (the /authorize redirect the user's browser follows).
ISAP_AUTH_PUBLIC_URL=https://<GATEWAY_SERVER_NAME>/auth
# Server-to-server base for POST /token — stays on the docker network.
ISAP_AUTH_INTERNAL_URL=http://isap-auth:9997/auth
# Must equal the app_id the bootstrap token was issued for.
ISAP_APP_ID=apihub
# Auto-create a role-less local user on first SSO login. Supersedes
# MCP_BRIDGE_AUTOCREATE, which is still honoured as an alias; this one flag now
# governs both the Admin UI and the MCP bridge.
SSO_AUTOCREATE=false
```

- [ ] **Step 3: 在 `deploy/apihub/docker-compose.yml` 新增 one-shot**

接在 `apihub-route-register` 區塊之後、`apihub-api` 之前插入：

```yaml
  # ── one-shot: self-register this app with isap-auth (idempotent) ──────────
  # Skips itself (exit 0) when .env.isap has no ISAP_REGISTRATION_TOKEN, so a
  # deployment that does not use SSO still starts normally.
  apihub-app-register:
    image: oracle-api-hub:latest
    container_name: apihub-app-register
    restart: "no"
    env_file: [.env, .env.isap]
    networks: [gateway-net]
    command: ["python", "-m", "scripts.register_isap_app"]
    depends_on:
      apihub-migrate:
        condition: service_completed_successfully
```

在 `apihub-api` 的 `depends_on` 中新增：

```yaml
      apihub-app-register:
        condition: service_completed_successfully
```

在 `apihub-api` 的 `environment` 區塊末尾新增（讓 `.env` 缺值時仍有安全預設）：

```yaml
      # Admin UI SSO via isap-auth. Off unless .env turns it on.
      ADMIN_SSO_ENABLED: ${ADMIN_SSO_ENABLED:-false}
      SSO_AUTOCREATE: ${SSO_AUTOCREATE:-false}
```

- [ ] **Step 4: 驗證 compose 語法**

Run: `cd deploy/apihub && docker compose config --quiet && echo OK`
Expected: 印出 `OK`（需先 `cp .env.example .env && cp .env.isap.example .env.isap`；若本機無 docker，改用 `python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml')); print('OK')"`）

- [ ] **Step 5: 對測試站重複 Step 1-4**

`deploy/apihub-test/` 完全同構，差異只有：
- 服務與容器名前綴 `apihub-test-`（`apihub-test-app-register`、依賴 `apihub-test-migrate`）
- `ISAP_APP_ID=apihub-test`
- `ISAP_APP_NAME=Oracle API Hub (Test)`
- 路徑前綴 `/apihub-test`：`APIHUB_PUBLIC_URL=https://<GATEWAY_SERVER_NAME>/apihub-test`、`ISAP_REDIRECT_URIS=https://<GATEWAY_SERVER_NAME>/apihub-test/api/v1/auth/sso/callback`

- [ ] **Step 6: 確認 `.env.isap` 不會被版控追蹤**

Run: `git check-ignore -v deploy/apihub/.env.isap`
Expected: 命中 `.gitignore` 中既有的 `.env` 規則。若沒有命中，在 `.gitignore` 新增 `deploy/*/.env.isap` 後再確認一次。

- [ ] **Step 7: Commit**

```bash
git add deploy/apihub/docker-compose.yml deploy/apihub/.env.example deploy/apihub/.env.isap.example deploy/apihub-test/docker-compose.yml deploy/apihub-test/.env.example deploy/apihub-test/.env.isap.example .gitignore
git commit -m "feat(deploy): wire the isap-auth self-registration one-shot

Adds apihub-app-register (and the apihub-test twin) following the platform
CONTRACT.md one-shot convention, plus the .env.isap file that keeps the
bootstrap token out of the long-running containers.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 文件

**Files:**
- Modify: `deploy/apihub/README.md`、`deploy/apihub-test/README.md`
- Modify: `README.md`、`docs/system_specification.md`
- Modify: `CLAUDE.md`、`AGENTS.md`
- Modify: `app/admin/CLAUDE.md`

- [ ] **Step 1: 在兩份 deploy README 新增「isap-auth 前置條件」節**

置於 MCP 章節的部署步驟之前：

```markdown
### isap-auth prerequisites

Both MCP OAuth and Admin UI SSO depend on isap-auth. Two separate things must
be true before either works.

**1. Dynamic client registration must be enabled (MCP only).**

MCP clients (Claude Code and friends) have no pre-agreed `client_id`: they bind
a fresh ephemeral loopback port per attempt and register themselves through RFC
7591. That endpoint is `POST /auth/api/v1/register` on isap-auth, gated by
`OAUTH_DCR_ENABLED`, which **defaults to `False`**. With it off, client
registration answers `403 registration_disabled` and step 4 of the MCP
verification checklist below fails at the browser SSO stage.

This is a platform-level decision, not one this repo can make: the flag affects
every app on that authorization server, the endpoint takes no credential, and
isap-auth's `ci/policy-check.sh` greps to keep the default off. Ask the
isap-auth operators to enable it, and — per isap-auth README A.6 — to restrict
`/auth/api/v1/register` at the gateway to internal networks at the same time.

Admin UI SSO does **not** need this flag: it uses the bootstrap-token path
(`PUT /apps/{app_id}`), which is always available.

**2. This app must be registered (Admin UI SSO only).**

`apihub-app-register` does it automatically on `docker compose up` — see
`.env.isap.example`. Mint its token on the isap-auth host first:

    docker exec isap-auth python -m app.cli issue-token apihub --name "Oracle API Hub"

| Symptom | Cause |
|---|---|
| MCP client registration gets `403 registration_disabled` | `OAUTH_DCR_ENABLED` is off |
| `apihub-app-register` exits 1 with a 401 | bootstrap token invalid or revoked |
| `apihub-app-register` exits 1 with a 403 | token was issued for a different `app_id` |
| `/authorize` answers `Unknown app_id or redirect_uri not allowed` | `APIHUB_PUBLIC_URL` and `ISAP_REDIRECT_URIS` disagree |
| `apihub-app-register` prints "skipped" | no `ISAP_REGISTRATION_TOKEN` in `.env.isap` |
```

（測試站版本把 `apihub` 換成 `apihub-test`。）

- [ ] **Step 2: 在兩份 deploy README 的驗證清單新增 SSO 步驟**

接在既有 MCP 驗證清單之後：

```markdown
### Admin UI SSO verification (requires live environment)

1. `docker compose logs apihub-app-register` — shows `Registered 'apihub'` with
   the expected redirect_uri, exit 0.
2. Open `https://<GATEWAY_SERVER_NAME>/apihub/admin` — the login page shows
   "Sign in with ISAP SSO" above the password form.
3. Click it — the browser reaches isap-auth (already-logged-in users bounce
   straight back), returns to the Admin UI signed in, and the address bar has
   **no** `#sso_token=` left in it.
4. The user badge shows the SSO username, not a shared service account.
5. With `SSO_AUTOCREATE=false`, an SSO user with no API Hub account lands back
   on the login page with "Ask an administrator to create one".
```

- [ ] **Step 3: 更新 `README.md` 與 `docs/system_specification.md`**

Run: 使用 `update-spec` skill，說明本次新增：Admin UI ISAP SSO 登入（方法 1 app-code 流程、fragment 交付、state 防護）、isap-auth 自註冊 one-shot、`SSO_AUTOCREATE` 取代 `MCP_BRIDGE_AUTOCREATE`（別名保留）、新增的環境變數表。

- [ ] **Step 4: 更新 `CLAUDE.md` 與 `AGENTS.md`**

在 `CLAUDE.md` 的 **Request Pipeline — Auth, Rate Limiting, Audit** 一節末尾新增：

```markdown
Two SSO entry points share `app/services/sso_service.py::resolve_sso_user`, so
the provisioning policy (`SSO_AUTOCREATE`, with `MCP_BRIDGE_AUTOCREATE` kept as
an alias) cannot drift between them: `POST /api/v1/auth/sso-token` (the MCP
identity bridge, guarded by a shared secret) and `GET /api/v1/auth/sso/callback`
(the Admin UI, guarded by a state cookie against login CSRF). The Admin UI pair
404s unless `ADMIN_SSO_ENABLED` is on, and hands the minted JWT to the SPA in a
URL fragment — never a query parameter, which would reach nginx logs.
```

把同一段鏡射到 `AGENTS.md` 的對應位置（兩檔必須同步，見 Code Conventions）。

- [ ] **Step 5: 更新 `app/admin/CLAUDE.md`**

新增登入頁的 SSO 分支說明：`window.__SSO_ENABLED__` 由 `admin_spa()` 注入決定按鈕是否顯示；`consumeSsoFragment()` 在 bootstrap 最早期執行並以 `history.replaceState` 清除 fragment；`SSO_ERRORS` 的 key 必須與 `app/api/v1/auth.py` 的 `sso_error=` slug 同步。

- [ ] **Step 6: 跑完整測試套件**

Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add README.md docs/system_specification.md CLAUDE.md AGENTS.md app/admin/CLAUDE.md deploy/apihub/README.md deploy/apihub-test/README.md
git commit -m "docs: document isap-auth prerequisites, self-registration and Admin UI SSO

Adds the OAUTH_DCR_ENABLED prerequisite that MCP OAuth needs (it defaults
off on isap-auth and is a platform-level decision), the bootstrap-token
registration path, and post-deployment verification steps for both.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review 結果

**Spec 覆蓋**：W1 → Task 6 Step 1-2；W2 → Task 2 + Task 5；W3 → Task 1 + Task 3 + Task 4；測試策略 → 各 task 內含，`test_sso_token_bridge` 回歸併入 Task 1 Step 7（既有測試檔已涵蓋 bridge secret 行為，不另建新檔）。

**規劃期發現、已回寫 spec 的缺口**：`APIHUB_PUBLIC_URL`。isap-auth 逐字比對 `redirect_uri` 與註冊值，`ROOT_PATH` 只是路徑前綴組不出絕對 URL。

**型別一致性**：`resolve_sso_user(db, username, email="")`、`exchange_sso_code(code, *, transport=None)`、`SsoExchangeError.reason`、`SSO_STATE_COOKIE` 在 Task 1/3/4 的用法一致；fragment slug（`missing_code`、`state_mismatch`、`auth_unreachable`、`invalid_code`、`not_provisioned`、`not_eligible`）在 service、endpoint、JS `SSO_ERRORS`、測試四處相同。
