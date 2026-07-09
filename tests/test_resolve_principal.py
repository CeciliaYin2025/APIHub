"""Direct unit tests for resolve_principal (Task 3, app/services/auth_service.py).

Uses a fake DB (same style as tests/test_api_key_validation.py) and a fake
request exposing `.headers` (dict) and `.client.host`, so no real Postgres or
HTTP stack is needed.
"""

from app.core.security import create_access_token, hash_api_key
from app.models.user import ApiKey, User
from app.services.auth_service import resolve_principal

RAW_KEY = "ahk_testkey123"


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeDB:
    """Dispatches db.query(Model) to a per-model canned row."""

    def __init__(self, api_key_row=None, user_row=None):
        self._api_key_row = api_key_row
        self._user_row = user_row
        self.committed = False

    def query(self, model):
        if model is ApiKey:
            return _FakeQuery(self._api_key_row)
        if model is User:
            return _FakeQuery(self._user_row)
        return _FakeQuery(None)

    def commit(self):
        self.committed = True


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, headers=None, client_host="10.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host)


def _service_user(is_active=True):
    return User(username="svc", is_active=is_active, is_service_account=True, is_superuser=False, roles=[])


def _api_key_row(**over):
    user = over.pop("user", _service_user())
    defaults = dict(
        id=1,
        user_id=1,
        name="k",
        key_prefix=RAW_KEY[:16],
        key_hash=hash_api_key(RAW_KEY),
        enabled=True,
        expires_at=None,
        ip_allowlist=None,
        rate_limit_per_minute=None,
    )
    defaults.update(over)
    k = ApiKey(**defaults)
    k.user = user
    return k


def test_x_api_key_header_routes_to_api_key_resolution():
    request = _FakeRequest(headers={"X-API-Key": RAW_KEY})
    db = _FakeDB(api_key_row=_api_key_row())

    principal = resolve_principal(db, request)

    assert principal is not None
    assert principal.type == "api_key"
    assert principal.user.username == "svc"


def test_bearer_api_key_prefix_routes_to_api_key_resolution():
    request = _FakeRequest(headers={"Authorization": f"Bearer {RAW_KEY}"})
    db = _FakeDB(api_key_row=_api_key_row())

    principal = resolve_principal(db, request)

    assert principal is not None
    assert principal.type == "api_key"
    assert principal.user.username == "svc"


def test_bearer_valid_jwt_returns_user_principal():
    token = create_access_token({"sub": "alice"})
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})
    user = User(username="alice", is_active=True, is_service_account=False, is_superuser=False, roles=[])
    db = _FakeDB(user_row=user)

    principal = resolve_principal(db, request)

    assert principal is not None
    assert principal.type == "user"
    assert principal.user.username == "alice"


def test_bearer_malformed_jwt_returns_none():
    request = _FakeRequest(headers={"Authorization": "Bearer not-a-real-jwt-token"})
    db = _FakeDB()

    assert resolve_principal(db, request) is None


def test_bearer_valid_jwt_unknown_user_returns_none():
    token = create_access_token({"sub": "ghost"})
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})
    db = _FakeDB(user_row=None)

    assert resolve_principal(db, request) is None


def test_bearer_valid_jwt_inactive_user_returns_none():
    token = create_access_token({"sub": "disabled"})
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})
    user = User(username="disabled", is_active=False, is_service_account=False, is_superuser=False, roles=[])
    db = _FakeDB(user_row=user)

    assert resolve_principal(db, request) is None
