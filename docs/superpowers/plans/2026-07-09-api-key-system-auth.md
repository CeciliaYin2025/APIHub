# API Key 系統認證 + 稽核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓外部應用系統以綁定服務帳號的 API key 呼叫 API（沿用現有 RBAC），並對每個請求記錄 principal 層級稽核（log 行 + PostgreSQL 表）。

**Architecture:** 憑證解析提前到 `AuthContextMiddleware`，一次解析（X-API-Key 或 Bearer JWT/`ahk_` key）→ 寫 `request.state.principal`；`get_current_user` 退化為讀 state；限流 middleware 依 principal 套 per-key 限額；`AuditMiddleware` 於最外層計時並寫稽核。API key 綁定到 `is_service_account` 的 `User`，授權完全沿用 `PermissionChecker`。

**Tech Stack:** FastAPI、Starlette `BaseHTTPMiddleware`、SQLAlchemy ORM（PostgreSQL 平台庫）、python-jose（JWT）、`hashlib.sha256` + `secrets`、pytest（純單元 + FastAPI `TestClient`）。

## Global Constraints

- Python 3.10+；line length 120；以 `ruff check` / `ruff format` 通過。
- 平台庫（user/permission/api-key/audit）永遠是 PostgreSQL，經 `app/db/session.py` 的 SQLAlchemy ORM。
- API key 明文前綴固定 `ahk_`；DB 只存 **SHA-256** 雜湊（`key_hash`）與前 16 字元（`key_prefix`）；明文只在建立時回傳一次。
- 服務帳號（`is_service_account=true`）強制 `is_superuser=false`，且不可經 `/auth/token` 帳密登入。
- 稽核寫入必須**非致命**：失敗只記 error log，絕不影響請求回應。
- 完全向後相容：現有 human JWT 流程與行為不得改變。
- 測試遵循現有慣例：純單元 + fake DB（見 `tests/test_authenticate.py`）；需要 ASGI 行為時用 `fastapi.testclient.TestClient` 搭迷你 app，不連真實 DB（以 monkeypatch 隔離 DB）。

---

### Task 1: API key 產生與雜湊工具

**Files:**
- Modify: `app/core/security.py`
- Test: `tests/test_api_key_security.py`

**Interfaces:**
- Produces:
  - `API_KEY_PREFIX: str = "ahk_"`
  - `generate_api_key() -> tuple[str, str, str]` 回 `(plaintext, key_prefix, key_hash)`
  - `hash_api_key(plaintext: str) -> str` 回 SHA-256 hex

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_api_key_security.py
"""Tests for API key generation and hashing."""

from app.core.security import API_KEY_PREFIX, generate_api_key, hash_api_key


def test_generate_api_key_shape():
    plaintext, key_prefix, key_hash = generate_api_key()
    assert plaintext.startswith(API_KEY_PREFIX)
    assert len(plaintext) > 20
    assert key_prefix == plaintext[:16]
    assert len(key_hash) == 64  # sha256 hex
    assert key_hash == hash_api_key(plaintext)


def test_generate_api_key_is_unique():
    a, _, _ = generate_api_key()
    b, _, _ = generate_api_key()
    assert a != b


def test_hash_api_key_is_stable():
    assert hash_api_key("ahk_abc") == hash_api_key("ahk_abc")
    assert hash_api_key("ahk_abc") != hash_api_key("ahk_xyz")
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_api_key_security.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_api_key'`

- [ ] **Step 3: 實作**

在 `app/core/security.py` 頂部 import 區加入，並於檔尾新增函式：

```python
import hashlib
import secrets

API_KEY_PREFIX = "ahk_"


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, key_prefix, key_hash) for a fresh API key.

    Plaintext is shown to the caller once; only key_prefix (display) and
    key_hash (SHA-256 hex, for lookup) are persisted.
    """
    plaintext = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, plaintext[:16], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    """SHA-256 hex of an API key plaintext (high-entropy → no bcrypt needed)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_api_key_security.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/test_api_key_security.py
git commit -m "feat(auth): API key generation and SHA-256 hashing utilities"
```

---

### Task 2: ORM models（is_service_account、ApiKey、AuditLog）+ migration

**Files:**
- Modify: `app/models/user.py`
- Create: `app/models/audit_log.py`
- Modify: `scripts/init_db.py`
- Test: `tests/test_api_key_models.py`

**Interfaces:**
- Consumes: `app.db.session.Base`
- Produces:
  - `User.is_service_account: bool`
  - `ApiKey`（`app.models.user`）欄位見下；`ApiKey.user` relationship（`lazy="joined"`）
  - `AuditLog`（`app.models.audit_log`）欄位見下

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_api_key_models.py
"""Tests that new ORM models expose the expected columns."""

from app.models.audit_log import AuditLog
from app.models.user import ApiKey, User


def test_user_has_service_account_flag():
    u = User(username="svc", is_service_account=True)
    assert u.is_service_account is True


def test_apikey_columns_and_table():
    assert ApiKey.__tablename__ == "api_hub_api_keys"
    key = ApiKey(user_id=1, name="k", key_prefix="ahk_abc", key_hash="h")
    assert key.name == "k"
    for col in ("enabled", "expires_at", "ip_allowlist", "rate_limit_per_minute",
                "last_used_at", "created_by", "revoked_at"):
        assert hasattr(key, col)


def test_auditlog_columns_and_table():
    assert AuditLog.__tablename__ == "api_hub_audit_logs"
    row = AuditLog(principal_type="api_key", principal="svc", method="POST", path="/x")
    for col in ("ts", "api_key_id", "api_key_name", "client_ip", "db_alias",
                "action", "status_code", "duration_ms"):
        assert hasattr(row, col)
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_api_key_models.py -v`
Expected: FAIL — `ImportError` / `cannot import name 'ApiKey'`

- [ ] **Step 3a: User 加旗標 + ApiKey model**

在 `app/models/user.py`：import 區改為包含 `JSON` 相關型別與 `func`（已 import `func`）。頂部 import 加：

```python
from sqlalchemy.dialects.postgresql import JSONB
```

`User` class 內（`is_superuser` 之後）新增一欄：

```python
    is_service_account = Column(Boolean, default=False, nullable=False)
```

於檔案結尾新增 model：

```python
class ApiKey(Base):
    __tablename__ = "api_hub_api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("api_hub_users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    key_prefix = Column(String(16), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    ip_allowlist = Column(JSONB, nullable=True)  # list[str] of CIDRs; null/empty = unrestricted
    rate_limit_per_minute = Column(Integer, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", lazy="joined")
```

- [ ] **Step 3b: AuditLog model**

```python
# app/models/audit_log.py
"""ORM model for per-request audit trail (platform PostgreSQL store)."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "api_hub_audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(DateTime, server_default=func.now(), index=True)
    principal_type = Column(String(20))   # "user" | "api_key" | "anonymous"
    principal = Column(String(100), index=True)
    api_key_id = Column(Integer, nullable=True)
    api_key_name = Column(String(100), nullable=True)
    client_ip = Column(String(64))
    method = Column(String(10))
    path = Column(String(500))
    db_alias = Column(String(64), nullable=True)
    action = Column(String(200), nullable=True)
    status_code = Column(Integer)
    duration_ms = Column(Integer)
```

- [ ] **Step 3c: init_db.py migration**

在 `scripts/init_db.py` 的 import 區補上（讓 `create_all` 認得新表）：

```python
from app.models.audit_log import AuditLog  # noqa: F401 — registers model
from app.models.user import ApiKey, Permission, Role, User  # noqa: F401 — registers models
```

（把原本的 `from app.models.user import Permission, Role, User` 這行替換為含 `ApiKey` 的版本。）

在 `main()` 中 `Base.metadata.create_all(bind=engine)` 之後、開 session 之前，加入冪等欄位新增：

```python
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE api_hub_users "
            "ADD COLUMN IF NOT EXISTS is_service_account boolean DEFAULT false NOT NULL"
        ))
    print("Ensured api_hub_users.is_service_account column.")
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_api_key_models.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/models/user.py app/models/audit_log.py scripts/init_db.py tests/test_api_key_models.py
git commit -m "feat(auth): ApiKey + AuditLog models, is_service_account column, migration"
```

---

### Task 3: Principal + API key 驗證邏輯

**Files:**
- Modify: `app/services/auth_service.py`
- Test: `tests/test_api_key_validation.py`

**Interfaces:**
- Consumes: `hash_api_key`（Task 1）、`ApiKey`/`User`（Task 2）
- Produces:
  - `Principal`（dataclass）：`type: str`, `user: User`, `key_id: int | None`, `key_name: str | None`, `rate_limit: int | None`
  - `class ApiKeyError(Exception)`：`status_code: int`, `detail: str`
  - `resolve_api_key(db, raw_key: str, client_ip: str) -> Principal`（驗證失敗 raise `ApiKeyError`）
  - `resolve_principal(db, request) -> Principal | None`（無憑證/JWT 無效回 None；API key 明確錯誤 raise `ApiKeyError`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_api_key_validation.py
"""Tests for API key validation (resolve_api_key)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_api_key
from app.models.user import ApiKey, Permission, Role, User
from app.services.auth_service import ApiKeyError, resolve_api_key

RAW = "ahk_testkey123"


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row
        self.committed = False

    def query(self, _model):
        return _FakeQuery(self._row)

    def commit(self):
        self.committed = True


def _service_user(is_active=True):
    role = Role(name="r", permissions=[Permission(resource="query:*", action="read")])
    return User(username="svc", is_active=is_active, is_service_account=True,
                is_superuser=False, roles=[role])


def _key(**over):
    user = over.pop("user", _service_user())
    defaults = dict(id=1, user_id=1, name="k", key_prefix="ahk_test",
                    key_hash=hash_api_key(RAW), enabled=True, expires_at=None,
                    ip_allowlist=None, rate_limit_per_minute=None)
    defaults.update(over)
    k = ApiKey(**defaults)
    k.user = user
    return k


def test_valid_key_returns_principal():
    p = resolve_api_key(_FakeDB(_key()), RAW, "10.0.0.1")
    assert p.type == "api_key"
    assert p.user.username == "svc"
    assert p.key_id == 1


def test_unknown_key_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(None), RAW, "10.0.0.1")
    assert e.value.status_code == 401


def test_disabled_key_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(enabled=False)), RAW, "10.0.0.1")
    assert e.value.status_code == 401
    assert "disabled" in e.value.detail


def test_expired_key_raises_401():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(expires_at=past)), RAW, "10.0.0.1")
    assert "expired" in e.value.detail


def test_future_expiry_is_valid():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    p = resolve_api_key(_FakeDB(_key(expires_at=future)), RAW, "10.0.0.1")
    assert p.type == "api_key"


def test_ip_not_in_allowlist_raises_403():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(ip_allowlist=["10.1.0.0/16"])), RAW, "192.168.0.5")
    assert e.value.status_code == 403


def test_ip_in_allowlist_is_valid():
    p = resolve_api_key(_FakeDB(_key(ip_allowlist=["10.1.0.0/16"])), RAW, "10.1.2.3")
    assert p.type == "api_key"


def test_inactive_service_account_raises_401():
    with pytest.raises(ApiKeyError) as e:
        resolve_api_key(_FakeDB(_key(user=_service_user(is_active=False))), RAW, "10.0.0.1")
    assert e.value.status_code == 401
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_api_key_validation.py -v`
Expected: FAIL — `cannot import name 'ApiKeyError'`

- [ ] **Step 3: 實作**

在 `app/services/auth_service.py` import 區加入：

```python
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.security import API_KEY_PREFIX, hash_api_key
from app.models.user import ApiKey
```

（保留既有 import；`User` 已 import。）於檔案內 `oauth2_scheme` 定義之後新增：

```python
@dataclass
class Principal:
    """Resolved caller identity attached to request.state.principal."""

    type: str                    # "user" | "api_key"
    user: User
    key_id: int | None = None
    key_name: str | None = None
    rate_limit: int | None = None


class ApiKeyError(Exception):
    """Raised on a present-but-invalid API key (short-circuits the request)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _ip_allowed(client_ip: str, allowlist: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in allowlist:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def resolve_api_key(db, raw_key: str, client_ip: str) -> Principal:
    """Validate an API key and return a Principal, or raise ApiKeyError."""
    row = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(raw_key)).first()
    if row is None:
        raise ApiKeyError(401, "Could not validate credentials")
    if not row.enabled:
        raise ApiKeyError(401, "API key is disabled")
    if row.expires_at is not None:
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise ApiKeyError(401, "API key has expired")
    if row.ip_allowlist and not _ip_allowed(client_ip, row.ip_allowlist):
        raise ApiKeyError(403, "Source IP not allowed")
    user = row.user
    if user is None or not user.is_active:
        raise ApiKeyError(401, "Could not validate credentials")

    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return Principal(
        type="api_key", user=user, key_id=row.id,
        key_name=row.name, rate_limit=row.rate_limit_per_minute,
    )


def resolve_principal(db, request) -> Principal | None:
    """Resolve caller from X-API-Key or Authorization: Bearer.

    Returns None for no/invalid JWT credentials. Raises ApiKeyError for a
    present-but-invalid API key.
    """
    client_ip = request.client.host if request.client else "unknown"
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return resolve_api_key(db, api_key, client_ip)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):]
    if token.startswith(API_KEY_PREFIX):
        return resolve_api_key(db, token, client_ip)

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
    except JWTError:
        return None
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        return None
    return Principal(type="user", user=user, rate_limit=None)
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_api_key_validation.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_api_key_validation.py
git commit -m "feat(auth): Principal + resolve_api_key/resolve_principal validation"
```

---

### Task 4: 服務帳號不可帳密登入

**Files:**
- Modify: `app/services/auth_service.py`
- Test: `tests/test_service_account_login.py`

**Interfaces:**
- Modifies: `authenticate_user(db, username, password) -> User | None` — 服務帳號一律回 None

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_service_account_login.py
"""Service accounts must not be able to log in via password flow."""

from app.core.security import get_password_hash
from app.models.user import User
from app.services.auth_service import authenticate_user

PASSWORD = "pw12345"


class _FakeQuery:
    def __init__(self, user):
        self._user = user

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._user


class _FakeDB:
    def __init__(self, user):
        self._user = user

    def query(self, _model):
        return _FakeQuery(self._user)


def test_service_account_password_login_rejected():
    svc = User(username="svc", hashed_password=get_password_hash(PASSWORD),
               is_active=True, is_service_account=True)
    assert authenticate_user(_FakeDB(svc), "svc", PASSWORD) is None


def test_human_account_still_authenticates():
    human = User(username="alice", hashed_password=get_password_hash(PASSWORD),
                 is_active=True, is_service_account=False)
    assert authenticate_user(_FakeDB(human), "alice", PASSWORD) is human
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_service_account_login.py -v`
Expected: FAIL — `test_service_account_password_login_rejected`（回傳 svc 而非 None）

- [ ] **Step 3: 實作**

在 `authenticate_user`（`app/services/auth_service.py`）中，`verify_password` 檢查通過後、回傳 user 前，加入服務帳號閘門。將函式體改為：

```python
def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    if user.is_service_account:
        # Service accounts authenticate only via API key, never password flow.
        return None
    return user
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_service_account_login.py tests/test_authenticate.py -v`
Expected: PASS（原有 authenticate 測試不受影響）

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_service_account_login.py
git commit -m "feat(auth): reject password login for service accounts"
```

---

### Task 5: get_current_user 改讀 request.state.principal

**Files:**
- Modify: `app/services/auth_service.py`
- Test: `tests/test_get_current_user.py`

**Interfaces:**
- Modifies: `get_current_user(request, _jwt=Depends(...), _key=Depends(...)) -> User` — 讀 `request.state.principal`，None 則 401

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_get_current_user.py
"""get_current_user resolves from request.state.principal."""

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.services.auth_service import Principal, get_current_user


class _State:
    def __init__(self, principal):
        self.principal = principal


class _Request:
    def __init__(self, principal):
        self.state = _State(principal)


def test_returns_user_from_principal():
    user = User(username="svc", is_active=True)
    req = _Request(Principal(type="api_key", user=user, key_id=1))
    assert get_current_user(req, None, None) is user


def test_missing_principal_raises_401():
    req = _Request(None)
    with pytest.raises(HTTPException) as e:
        get_current_user(req, None, None)
    assert e.value.status_code == 401
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_get_current_user.py -v`
Expected: FAIL — 現行 `get_current_user` 簽章為 `(token, db)`，呼叫方式不符

- [ ] **Step 3: 實作**

在 `app/services/auth_service.py`：

1) import 區加入 `Request`、`APIKeyHeader`（把現有的 `from fastapi import ...` 與 `from fastapi.security import OAuth2PasswordBearer` 兩行替換為）：

```python
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
```

2) 將 `oauth2_scheme` 改為 `auto_error=False`，並新增 api key header 方案：

```python
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix.lstrip('/')}/auth/token", auto_error=False
)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)
```

3) 將 `get_current_user` 整個替換為：

```python
def get_current_user(
    request: Request,
    _jwt: str | None = Depends(oauth2_scheme),
    _key: str | None = Depends(api_key_scheme),
) -> User:
    """Return the caller resolved by AuthContextMiddleware.

    The two Depends params are declared only so Swagger shows both auth
    schemes and forwards the credentials; the actual resolution happens in
    AuthContextMiddleware, which sets request.state.principal.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal.user
```

> 註：`get_current_user` 不再需要 `db` 參數與 `jwt.decode`（已移到 `resolve_principal`）。`PermissionChecker` 仍 `Depends(get_current_user)`，簽章改變不影響其用法。

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_get_current_user.py tests/test_permissions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_get_current_user.py
git commit -m "feat(auth): get_current_user reads request.state.principal"
```

---

### Task 6: AuthContextMiddleware

**Files:**
- Create: `app/middleware/auth_context.py`
- Test: `tests/test_auth_context_middleware.py`

**Interfaces:**
- Consumes: `resolve_principal`、`ApiKeyError`（Task 3）；`app.db.session.SessionLocal`
- Produces:
  - `build_principal(request) -> Principal | None`（自管 session；raise `ApiKeyError`）
  - `AuthContextMiddleware`（Starlette `BaseHTTPMiddleware`）：設 `request.state.principal`，`ApiKeyError` 短路回 JSON

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_auth_context_middleware.py
"""AuthContextMiddleware sets request.state.principal and short-circuits ApiKeyError."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.middleware.auth_context as ac
from app.middleware.auth_context import AuthContextMiddleware
from app.models.user import User
from app.services.auth_service import ApiKeyError, Principal


def _make_app():
    application = FastAPI()
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        p = request.state.principal
        return {"who": None if p is None else p.user.username}

    return application


def test_principal_set_when_resolved(monkeypatch):
    user = User(username="svc", is_active=True)
    monkeypatch.setattr(ac, "build_principal",
                        lambda request: Principal(type="api_key", user=user, key_id=1))
    client = TestClient(_make_app())
    assert client.get("/probe").json() == {"who": "svc"}


def test_principal_none_when_unresolved(monkeypatch):
    monkeypatch.setattr(ac, "build_principal", lambda request: None)
    client = TestClient(_make_app())
    assert client.get("/probe").json() == {"who": None}


def test_apikey_error_short_circuits(monkeypatch):
    def _raise(request):
        raise ApiKeyError(403, "Source IP not allowed")

    monkeypatch.setattr(ac, "build_principal", _raise)
    client = TestClient(_make_app())
    resp = client.get("/probe")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Source IP not allowed"
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_auth_context_middleware.py -v`
Expected: FAIL — `No module named 'app.middleware.auth_context'`

- [ ] **Step 3: 實作**

```python
# app/middleware/auth_context.py
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
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_auth_context_middleware.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/middleware/auth_context.py tests/test_auth_context_middleware.py
git commit -m "feat(auth): AuthContextMiddleware resolves principal per request"
```

---

### Task 7: RateLimiterMiddleware 支援 per-key 限額

**Files:**
- Modify: `app/middleware/rate_limiter.py`
- Test: `tests/test_rate_limiter_perkey.py`

**Interfaces:**
- Consumes: `request.state.principal`（Task 3/6）
- Modifies: `RateLimiterMiddleware.dispatch` — principal 為 api_key 時以 `api_key:{id}` 為限流 key、套該 key 限額

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_rate_limiter_perkey.py
"""RateLimiterMiddleware keys on api_key principal and honors per-key limit."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth_context import AuthContextMiddleware
import app.middleware.auth_context as ac
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.models.user import User
from app.services.auth_service import Principal


def _app_with_key(monkeypatch, rate_limit):
    application = FastAPI()
    # inner: rate limiter; outer: auth context (added last = outer)
    application.add_middleware(RateLimiterMiddleware, max_requests=1000)
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        return {"ok": True}

    user = User(username="svc", is_active=True)
    monkeypatch.setattr(ac, "build_principal", lambda request: Principal(
        type="api_key", user=user, key_id=7, rate_limit=rate_limit))
    return application


def test_per_key_limit_enforced(monkeypatch):
    client = TestClient(_app_with_key(monkeypatch, rate_limit=2))
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429


def test_null_key_limit_falls_back_to_global(monkeypatch):
    client = TestClient(_app_with_key(monkeypatch, rate_limit=None))
    # global max_requests=1000 → 3 quick calls all pass
    for _ in range(3):
        assert client.get("/probe").status_code == 200
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_rate_limiter_perkey.py -v`
Expected: FAIL — 現行限流只用 IP，`test_per_key_limit_enforced` 不會回 429

- [ ] **Step 3: 實作**

將 `app/middleware/rate_limiter.py` 的 `dispatch` 整段替換為：

```python
    async def dispatch(self, request: Request, call_next) -> Response:
        principal = getattr(request.state, "principal", None)
        if principal is not None and principal.type == "api_key":
            key = f"api_key:{principal.key_id}"
            limit = principal.rate_limit or self.max_requests
        else:
            client_ip = request.client.host if request.client else "unknown"
            key = f"ip:{client_ip}"
            limit = self.max_requests

        now = time.time()
        self._hits[key] = [t for t in self._hits[key] if now - t < self.window]

        if len(self._hits[key]) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._hits[key].append(now)
        return await call_next(request)
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_rate_limiter_perkey.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add app/middleware/rate_limiter.py tests/test_rate_limiter_perkey.py
git commit -m "feat(auth): per-API-key rate limiting"
```

---

### Task 8: AuditMiddleware（log 行 + 稽核表，非致命）

**Files:**
- Create: `app/middleware/audit.py`
- Test: `tests/test_audit_middleware.py`

**Interfaces:**
- Consumes: `request.state.principal`；`request.state.audit_db_alias` / `request.state.audit_action`（可選，Task 10/11 回填）；`app.db.session.SessionLocal`；`AuditLog`（Task 2）
- Produces:
  - `build_audit_row(request, response, duration_ms) -> dict`
  - `write_audit(row: dict) -> None`（自管 session；失敗吞掉）
  - `AuditMiddleware`（`BaseHTTPMiddleware`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_audit_middleware.py
"""AuditMiddleware records principal/action and never breaks the request."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.middleware.audit as au
from app.middleware.audit import AuditMiddleware, build_audit_row
from app.models.user import User
from app.services.auth_service import Principal


class _Resp:
    status_code = 200


class _State:
    principal = Principal(type="api_key", user=User(username="svc"), key_id=3, key_name="k")
    audit_db_alias = "hr"
    audit_action = "HR_QueryEmployeeAll"


class _Client:
    host = "10.0.0.9"


class _Req:
    method = "POST"
    url = type("U", (), {"path": "/api/v1/hr/queries/execute"})()
    client = _Client()
    state = _State()


def test_build_audit_row_fields():
    row = build_audit_row(_Req(), _Resp(), 42)
    assert row["principal_type"] == "api_key"
    assert row["principal"] == "svc"
    assert row["api_key_id"] == 3
    assert row["db_alias"] == "hr"
    assert row["action"] == "HR_QueryEmployeeAll"
    assert row["status_code"] == 200
    assert row["duration_ms"] == 42


def test_anonymous_when_no_principal():
    req = _Req()
    req.state = type("S", (), {"principal": None})()
    row = build_audit_row(req, _Resp(), 1)
    assert row["principal_type"] == "anonymous"
    assert row["principal"] == "anonymous"


def test_request_succeeds_even_if_audit_write_raises(monkeypatch):
    def _boom(row):
        raise RuntimeError("db down")

    monkeypatch.setattr(au, "write_audit", _boom)

    application = FastAPI()
    application.add_middleware(AuditMiddleware)

    @application.get("/ok")
    def ok():
        return {"ok": True}

    resp = TestClient(application).get("/ok")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_audit_middleware.py -v`
Expected: FAIL — `No module named 'app.middleware.audit'`

- [ ] **Step 3: 實作**

```python
# app/middleware/audit.py
"""Per-request audit: one structured log line + one api_hub_audit_logs row.

Audit writes are best-effort: any failure is logged and swallowed so it can
never affect the response.
"""

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger("app.audit")

# Paths that should not generate audit rows (health checks, token issuance,
# admin static assets). Matched by suffix / prefix against the full path.
_SKIP_SUFFIXES = ("/health", "/auth/token")


def _should_skip(path: str) -> bool:
    if "/admin" in path:
        return True
    return any(path.endswith(s) for s in _SKIP_SUFFIXES)


def build_audit_row(request: Request, response: Response, duration_ms: int) -> dict:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        p_type, p_name, key_id, key_name = "anonymous", "anonymous", None, None
    else:
        p_type = principal.type
        p_name = principal.user.username
        key_id = principal.key_id
        key_name = principal.key_name
    return {
        "principal_type": p_type,
        "principal": p_name,
        "api_key_id": key_id,
        "api_key_name": key_name,
        "client_ip": request.client.host if request.client else "unknown",
        "method": request.method,
        "path": request.url.path,
        "db_alias": getattr(request.state, "audit_db_alias", None),
        "action": getattr(request.state, "audit_action", None),
        "status_code": response.status_code,
        "duration_ms": duration_ms,
    }


def write_audit(row: dict) -> None:
    """Persist one audit row. Caller wraps this so failures are non-fatal."""
    db = SessionLocal()
    try:
        db.add(AuditLog(**row))
        db.commit()
    finally:
        db.close()


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        if _should_skip(request.url.path):
            return response
        try:
            row = build_audit_row(request, response, int((time.time() - start) * 1000))
            logger.info(
                "[AUDIT] principal=%s type=%s key=%s ip=%s %s %s db=%s action=%s status=%s dur=%sms",
                row["principal"], row["principal_type"], row["api_key_name"],
                row["client_ip"], row["method"], row["path"],
                row["db_alias"], row["action"], row["status_code"], row["duration_ms"],
            )
            write_audit(row)
        except Exception:  # noqa: BLE001 — audit must never break the request
            logger.exception("audit write failed")
        return response
```

> 註：測試 `test_request_succeeds_even_if_audit_write_raises` monkeypatch 的是模組層 `write_audit`；`dispatch` 內以 `au.write_audit` 名稱查找即可被替換（Python 於呼叫時查模組全域），故 `dispatch` 內直接呼叫 `write_audit(row)` 即可被 monkeypatch。

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_audit_middleware.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/middleware/audit.py tests/test_audit_middleware.py
git commit -m "feat(audit): AuditMiddleware writes non-fatal log + DB audit row"
```

---

### Task 9: api_key_service（建立/列表/撤銷）

**Files:**
- Create: `app/services/api_key_service.py`
- Test: `tests/test_api_key_service.py`

**Interfaces:**
- Consumes: `generate_api_key`（Task 1）；`ApiKey`/`User`（Task 2）
- Produces:
  - `create_api_key(db, *, service_account_username, name, expires_at=None, ip_allowlist=None, rate_limit_per_minute=None, created_by=None) -> tuple[ApiKey, str]`（回 `(row, plaintext)`；非服務帳號/查無 raise `ValueError`）
  - `list_api_keys(db) -> list[ApiKey]`
  - `revoke_api_key(db, key_id) -> ApiKey`（查無 raise `ValueError`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_api_key_service.py
"""Tests for API key service (create/list/revoke)."""

import pytest

from app.models.user import ApiKey, User
from app.services.api_key_service import create_api_key, revoke_api_key


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None, key=None):
        self._user = user
        self._key = key
        self.added = []

    def query(self, model):
        return _FakeQuery(self._key if model is ApiKey else self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _svc(is_service=True):
    return User(id=1, username="svc", is_active=True, is_service_account=is_service)


def test_create_returns_plaintext_and_row():
    db = _FakeDB(user=_svc())
    row, plaintext = create_api_key(db, service_account_username="svc", name="k")
    assert plaintext.startswith("ahk_")
    assert row.name == "k"
    assert row.key_hash and row.key_prefix
    assert row in db.added


def test_create_rejects_non_service_account():
    db = _FakeDB(user=_svc(is_service=False))
    with pytest.raises(ValueError, match="not a service account"):
        create_api_key(db, service_account_username="alice", name="k")


def test_create_rejects_unknown_account():
    db = _FakeDB(user=None)
    with pytest.raises(ValueError, match="not found"):
        create_api_key(db, service_account_username="ghost", name="k")


def test_revoke_disables_key():
    key = ApiKey(id=5, user_id=1, name="k", key_prefix="ahk_x", key_hash="h", enabled=True)
    row = revoke_api_key(_FakeDB(key=key), 5)
    assert row.enabled is False
    assert row.revoked_at is not None


def test_revoke_unknown_raises():
    with pytest.raises(ValueError, match="not found"):
        revoke_api_key(_FakeDB(key=None), 999)
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_api_key_service.py -v`
Expected: FAIL — `No module named 'app.services.api_key_service'`

- [ ] **Step 3: 實作**

```python
# app/services/api_key_service.py
"""Business logic for issuing, listing, and revoking API keys."""

from datetime import datetime, timezone

from app.core.security import generate_api_key
from app.models.user import ApiKey, User


def create_api_key(
    db,
    *,
    service_account_username: str,
    name: str,
    expires_at: datetime | None = None,
    ip_allowlist: list[str] | None = None,
    rate_limit_per_minute: int | None = None,
    created_by: str | None = None,
) -> tuple[ApiKey, str]:
    """Create a key bound to a service account. Returns (row, plaintext).

    Plaintext is returned once and never stored.
    """
    user = db.query(User).filter(User.username == service_account_username).first()
    if user is None:
        raise ValueError(f"Service account '{service_account_username}' not found")
    if not user.is_service_account:
        raise ValueError(f"User '{service_account_username}' is not a service account")

    plaintext, key_prefix, key_hash = generate_api_key()
    row = ApiKey(
        user_id=user.id,
        name=name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        expires_at=expires_at,
        ip_allowlist=ip_allowlist,
        rate_limit_per_minute=rate_limit_per_minute,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def list_api_keys(db) -> list[ApiKey]:
    return db.query(ApiKey).all()


def revoke_api_key(db, key_id: int) -> ApiKey:
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if row is None:
        raise ValueError("API key not found")
    row.enabled = False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_api_key_service.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/api_key_service.py tests/test_api_key_service.py
git commit -m "feat(auth): api_key_service create/list/revoke"
```

---

### Task 10: API 端點 + schema（api-keys CRUD、服務帳號規則）

**Files:**
- Create: `app/schemas/api_key.py`
- Modify: `app/schemas/auth.py`
- Modify: `app/api/v1/auth.py`
- Test: `tests/test_api_key_endpoints.py`

**Interfaces:**
- Consumes: `create_api_key`/`list_api_keys`/`revoke_api_key`（Task 9）；`get_current_active_superuser`
- Produces:
  - schema `ApiKeyCreate` / `ApiKeyOut` / `ApiKeyCreated`
  - route fns `create_api_key_endpoint` / `list_api_keys_endpoint` / `revoke_api_key_endpoint`
  - `UserCreate.is_service_account: bool = False`、`UserUpdate.is_service_account: bool | None = None`、`UserOut.is_service_account: bool`
  - helper `_key_to_out(row) -> dict`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_api_key_endpoints.py
"""Route-level tests for API key endpoints and service-account rules."""

import pytest
from fastapi import HTTPException

from app.api.v1.auth import (
    create_api_key_endpoint,
    create_user,
    revoke_api_key_endpoint,
)
from app.models.user import ApiKey, User
from app.schemas.api_key import ApiKeyCreate
from app.schemas.auth import UserCreate


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._result if isinstance(self._result, list) else []


class _FakeDB:
    def __init__(self, user=None, key=None):
        self._user = user
        self._key = key
        self.added = []

    def query(self, model):
        return _FakeQuery(self._key if model is ApiKey else self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _admin():
    return User(username="admin", is_superuser=True, is_active=True)


def _svc():
    return User(id=1, username="svc", is_active=True, is_service_account=True)


def test_create_api_key_returns_plaintext_once():
    db = _FakeDB(user=_svc())
    payload = ApiKeyCreate(service_account_username="svc", name="prod")
    result = create_api_key_endpoint(payload, db=db, admin=_admin())
    # endpoint returns a plain dict (response_model serialization only happens
    # through the app, not on a direct function call)
    assert result["api_key"].startswith("ahk_")
    assert result["name"] == "prod"


def test_create_api_key_for_non_service_account_400():
    db = _FakeDB(user=User(id=2, username="alice", is_service_account=False))
    payload = ApiKeyCreate(service_account_username="alice", name="x")
    with pytest.raises(HTTPException) as e:
        create_api_key_endpoint(payload, db=db, admin=_admin())
    assert e.value.status_code == 400


def test_revoke_unknown_key_404():
    with pytest.raises(HTTPException) as e:
        revoke_api_key_endpoint(999, db=_FakeDB(key=None), _admin=_admin())
    assert e.value.status_code == 404


def test_create_user_service_account_cannot_be_superuser():
    db = _FakeDB(user=None)  # username lookup returns None → no existing user
    payload = UserCreate(username="svc", password="x", is_service_account=True,
                         is_superuser=True)
    with pytest.raises(HTTPException) as e:
        create_user(payload, db=db, _admin=_admin())
    assert e.value.status_code == 400
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_api_key_endpoints.py -v`
Expected: FAIL — `cannot import name 'create_api_key_endpoint'` / `ApiKeyCreate`

- [ ] **Step 3a: schemas/api_key.py**

```python
# app/schemas/api_key.py
"""Pydantic schemas for API key management."""

from datetime import datetime

from pydantic import BaseModel


class ApiKeyCreate(BaseModel):
    service_account_username: str
    name: str
    expires_at: datetime | None = None
    ip_allowlist: list[str] | None = None
    rate_limit_per_minute: int | None = None


class ApiKeyOut(BaseModel):
    id: int
    name: str
    service_account: str
    key_prefix: str
    enabled: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    api_key: str  # plaintext, returned once at creation
```

- [ ] **Step 3b: schemas/auth.py 加 is_service_account**

在 `UserCreate` 加：`is_service_account: bool = False`
在 `UserUpdate` 加：`is_service_account: bool | None = None`
在 `UserOut` 加：`is_service_account: bool = False`

- [ ] **Step 3c: auth.py 端點與服務帳號規則**

在 `app/api/v1/auth.py` import 區加入：

```python
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services.api_key_service import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
```

在檔尾新增 helper 與端點：

```python
# ── API Keys (superuser only) ───────────────────────────────────────────────

def _key_to_out(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "service_account": row.user.username if row.user else "",
        "key_prefix": row.key_prefix,
        "enabled": row.enabled,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
    }


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key_endpoint(
    payload: ApiKeyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    try:
        row, plaintext = create_api_key(
            db,
            service_account_username=payload.service_account_username,
            name=payload.name,
            expires_at=payload.expires_at,
            ip_allowlist=payload.ip_allowlist,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            created_by=admin.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**_key_to_out(row), "api_key": plaintext}


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys_endpoint(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    return [_key_to_out(r) for r in list_api_keys(db)]


@router.delete("/api-keys/{key_id}", status_code=200)
def revoke_api_key_endpoint(
    key_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    try:
        revoke_api_key(db, key_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": f"API key {key_id} revoked"}
```

在 `create_user` 中，建立 `User(...)` 前加入規則並帶入旗標。將 `create_user` 內容改為：

```python
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    if payload.is_service_account and payload.is_superuser:
        raise HTTPException(status_code=400, detail="A service account cannot be a superuser")
    user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name,
        is_superuser=payload.is_superuser,
        is_service_account=payload.is_service_account,
    )
```

（其餘 `create_user`（role 指派、add/commit/refresh）不變。）

在 `update_user` 中，於處理 `is_superuser` 的區塊加入服務帳號互斥檢查。在 `if payload.is_superuser is not None:` 區塊內、設定前加：

```python
    if payload.is_service_account is not None:
        user.is_service_account = payload.is_service_account
    if user.is_service_account and (payload.is_superuser or user.is_superuser):
        # setting superuser on a service account (or vice versa) is forbidden
        if payload.is_superuser:
            raise HTTPException(status_code=400, detail="A service account cannot be a superuser")
```

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_api_key_endpoints.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add app/schemas/api_key.py app/schemas/auth.py app/api/v1/auth.py tests/test_api_key_endpoints.py
git commit -m "feat(auth): API key CRUD endpoints + service-account superuser guard"
```

---

### Task 11: main.py 註冊 middleware + db_alias/action 回填

**Files:**
- Modify: `app/main.py`
- Modify: `app/api/v1/databases.py`（`get_db_alias` 回填 `audit_db_alias`）
- Modify: `app/api/v1/queries.py`（execute 回填 `audit_action`）
- Test: `tests/test_middleware_wiring.py`

**Interfaces:**
- Consumes: `AuthContextMiddleware`（Task 6）、`AuditMiddleware`（Task 8）、擴充後的 `RateLimiterMiddleware`（Task 7）
- Produces: `app` 的 middleware 疊放順序（外→內）：CORS → Audit → AuthContext → RateLimiter → routers

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_middleware_wiring.py
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
```

- [ ] **Step 2: 執行確認失敗**

Run: `pytest tests/test_middleware_wiring.py -v`
Expected: FAIL — `AuthContextMiddleware`/`AuditMiddleware` 尚未註冊

- [ ] **Step 3a: main.py 註冊順序**

在 `app/main.py` import 區加入：

```python
from app.middleware.audit import AuditMiddleware
from app.middleware.auth_context import AuthContextMiddleware
```

將現有 middleware 區塊（CORS + RateLimiter）替換為以下順序（**後加者為外層**，故最後加 CORS 使其最外，Audit 次外）：

```python
# ── Middleware ────────────────────────────────────────────────────────────────
# Order matters. add_middleware stacks last-added = outermost. We want, outer→inner:
#   CORS → Audit → AuthContext → RateLimiter → routers
app.add_middleware(RateLimiterMiddleware)   # innermost of ours (reads principal)
app.add_middleware(AuthContextMiddleware)   # resolves principal before rate limiter
app.add_middleware(AuditMiddleware)         # times whole chain, sees final principal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 3b: databases.py 回填 db_alias**

將 `get_db_alias` 改為接受 `Request` 並回填稽核用 alias：

```python
from fastapi import APIRouter, Depends, HTTPException, Path, Request


def get_db_alias(request: Request, db: str = Path(..., description="Database alias")) -> str:
    """FastAPI dependency — validate that *db* is a registered database alias."""
    if not db_registry.has(db):
        available = ", ".join(db_registry.aliases()) or "(none)"
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db}' not configured. Available: {available}",
        )
    request.state.audit_db_alias = db
    return db
```

- [ ] **Step 3c: queries.py execute 回填 action**

在 `execute` route（`app/api/v1/queries.py`）簽章加入 `request: Request` 並回填 query_name。將簽章與開頭改為：

```python
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile  # add Request


@router.post("/{db}/queries/execute", response_model=QueryResponse)
def execute(
    request: Request,
    payload: QueryRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    request.state.audit_action = payload.query_name
    definition = sql_registry.get(payload.query_name, db_alias=db)
```

> 注意：原參數名為 `request: QueryRequest`。因 `Request` 佔用了 `request` 名稱，將 body 參數改名為 `payload`，並把函式體內所有 `request.query_name` / `request.params` / `request.fetch_mode` / `request.page` 等一律改為 `payload.` 前綴（共出現於 `sql_registry.get(...)`、`execute_query(...)`、`QueryResponse(query_name=payload.query_name, fetch_mode=payload.fetch_mode, ...)`）。逐一替換，勿遺漏。

- [ ] **Step 4: 執行確認通過**

Run: `pytest tests/test_middleware_wiring.py -v`
然後跑全套確保無回歸：`pytest -v`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/api/v1/databases.py app/api/v1/queries.py tests/test_middleware_wiring.py
git commit -m "feat(auth): wire audit/auth/rate-limit middleware + backfill audit db_alias/action"
```

---

### Task 12: Admin UI — API Keys 頁與服務帳號勾選

**Files:**
- Modify: `app/admin/index.html`
- Modify: `app/admin/js/app.js`

**Interfaces:**
- Consumes: 既有 `api(method, path, body)` helper、`toast()`、`navigateTo()`、page 註冊 map（見 `app.js:182-191`）
- Produces: 「API Keys」頁（列表 + 新增表單，建立後一次性顯示明文）；Users 表單「服務帳號」勾選（勾選時鎖 superuser）

> 本任務為前端 vanilla JS，無自動化測試框架；以手動冒煙驗證取代單元測試。所有呼叫走既有 `api()`（自帶 JWT）。

- [ ] **Step 1: 側欄與 page 註冊**

在 `app/admin/index.html` 側欄導覽（其他 `data-page` 連結旁）新增：

```html
<a href="#" class="nav-link" data-page="api-keys">API Keys</a>
```

在 `app/admin/js/app.js` 的標題 map（`app.js:182` 附近）加入：
```javascript
    'api-keys': 'API Keys',
```
在 renderer map（`app.js:190` 附近）加入：
```javascript
    'api-keys': renderApiKeys,
```

- [ ] **Step 2: renderApiKeys 頁面**

在 `app.js` 適當區塊（其他 `render*Console`/頁面函式旁）新增：

```javascript
/* ── API Keys ─────────────────────────────────────────────────────────── */
async function renderApiKeys() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>API Keys</h2></div>
      <div class="form-group">
        <button class="btn btn-primary" onclick="openApiKeyForm()">+ 新增 API Key</button>
      </div>
      <div id="apiKeyList"><p class="text-muted">Loading…</p></div>
    </div>
    <div id="apiKeyResult"></div>`;
  await loadApiKeys();
}

async function loadApiKeys() {
  const el = document.getElementById('apiKeyList');
  try {
    const keys = await api('GET', '/auth/api-keys');
    if (!keys.length) { el.innerHTML = '<p class="text-muted">尚無 API Key</p>'; return; }
    el.innerHTML = `
      <table class="table"><thead><tr>
        <th>Name</th><th>服務帳號</th><th>Prefix</th><th>Last Used</th><th>狀態</th><th></th>
      </tr></thead><tbody>${keys.map(k => `
        <tr>
          <td>${esc(k.name)}</td><td>${esc(k.service_account)}</td>
          <td><code>${esc(k.key_prefix)}…</code></td>
          <td>${k.last_used_at ? esc(k.last_used_at) : '—'}</td>
          <td>${k.enabled ? '啟用' : '已撤銷'}</td>
          <td>${k.enabled ? `<button class="btn btn-sm btn-danger" onclick="revokeApiKey(${k.id})">撤銷</button>` : ''}</td>
        </tr>`).join('')}</tbody></table>`;
  } catch (e) {
    el.innerHTML = `<p class="text-danger">載入失敗：${esc(e.message)}</p>`;
  }
}

window.openApiKeyForm = async function () {
  let accounts = [];
  try {
    const users = await api('GET', '/auth/users');
    accounts = users.filter(u => u.is_service_account);
  } catch (e) { toast('載入服務帳號失敗：' + e.message, 'error'); }
  const opts = accounts.map(u => `<option value="${esc(u.username)}">${esc(u.username)}</option>`).join('');
  showModal('新增 API Key', `
    <label>服務帳號</label>
    <select id="akUser">${opts || '<option value="">（請先建立服務帳號）</option>'}</select>
    <label>名稱</label><input id="akName" placeholder="hr-portal-prod">
    <label>到期日（可留空）</label><input id="akExpires" type="date">
    <label>IP 白名單（CIDR，逗號分隔，可留空）</label><input id="akIps" placeholder="10.1.0.0/16">
    <label>每分鐘限額（可留空）</label><input id="akRate" type="number">
    <button class="btn btn-primary" onclick="submitApiKey()">建立</button>`);
};

window.submitApiKey = async function () {
  const body = {
    service_account_username: document.getElementById('akUser').value,
    name: document.getElementById('akName').value.trim(),
    expires_at: document.getElementById('akExpires').value || null,
    ip_allowlist: (document.getElementById('akIps').value.trim()
      ? document.getElementById('akIps').value.split(',').map(s => s.trim()).filter(Boolean) : null),
    rate_limit_per_minute: (document.getElementById('akRate').value
      ? Number(document.getElementById('akRate').value) : null),
  };
  if (!body.service_account_username || !body.name) { toast('請填服務帳號與名稱', 'error'); return; }
  try {
    const created = await api('POST', '/auth/api-keys', body);
    closeModal();
    document.getElementById('apiKeyResult').innerHTML = `
      <div class="card"><div class="card-header"><h3>API Key 已建立</h3></div>
        <p class="text-danger"><strong>請立即複製，此明文只顯示這一次：</strong></p>
        <pre class="monospace" style="user-select:all">${esc(created.api_key)}</pre></div>`;
    await loadApiKeys();
  } catch (e) { toast('建立失敗：' + e.message, 'error'); }
};

window.revokeApiKey = async function (id) {
  try { await api('DELETE', `/auth/api-keys/${id}`); toast('已撤銷', 'success'); await loadApiKeys(); }
  catch (e) { toast('撤銷失敗：' + e.message, 'error'); }
};
```

> 若 `showModal`/`closeModal`/`esc`/`toast` 的實際名稱與此不同，改用專案既有 helper 名稱（於 `app.js` 搜尋 `function showModal`/`function esc`/`function toast` 確認）。

- [ ] **Step 3: Users 表單「服務帳號」勾選**

於使用者建立/編輯 modal 的表單（`app.js` 內產生 user 表單處）加入勾選框，並在勾選時停用 superuser 勾選：

```html
<label><input type="checkbox" id="uServiceAccount" onchange="document.getElementById('uSuperuser').disabled = this.checked; if(this.checked) document.getElementById('uSuperuser').checked=false;"> 服務帳號（僅能用 API Key，禁止 superuser）</label>
```

送出 user 的函式中，body 加入：
```javascript
    is_service_account: document.getElementById('uServiceAccount').checked,
```
（若編輯既有使用者，讀取時以 `user.is_service_account` 預設勾選狀態。）

- [ ] **Step 4: 手動冒煙驗證**

啟動：`uvicorn app.main:app --port 8000`，開 `http://localhost:8000/admin`，以 superuser 登入後：
1. Users 頁建立一個「服務帳號」（勾選服務帳號，確認 superuser 被鎖）。
2. API Keys 頁 → 新增 → 選該服務帳號、填名稱 → 建立 → 確認明文 key 顯示一次。
3. 用該 key 呼叫：`curl -H "X-API-Key: <明文>" http://localhost:8000/api/v1/databases`（該服務帳號需有對應權限）→ 應成功。
4. API Keys 頁按「撤銷」→ 再用同 key 呼叫 → 應回 401。
5. 查 `api_hub_audit_logs` 表應有對應請求列（principal=服務帳號、action/db_alias 有值）。

- [ ] **Step 5: Commit**

```bash
git add app/admin/index.html app/admin/js/app.js
git commit -m "feat(admin): API Keys management page + service-account toggle"
```

---

## 收尾驗證

- [ ] 全套測試：`pytest -v`（全綠）
- [ ] Lint：`ruff check app/ tests/` 與 `ruff format app/ tests/`
- [ ] 於乾淨環境跑 `python -m scripts.init_db`（或既有庫重跑）確認冪等 ALTER 與新表建立成功
- [ ] 手動驗證 Task 12 Step 4 全流程

## 前置/部署提醒（來自 spec §13）

- 正式環境**務必替換 `app/core/config.py` 的預設 `secret_key`**（HS256 對稱金鑰外洩即可偽造任意身分 token）。
- 稽核表保留策略（清理/分割）需於運維另行處理；本計畫不含清理程式。
- 多實例部署時 in-memory 限流需改 Redis-backed（既有限制，本計畫未處理）。
