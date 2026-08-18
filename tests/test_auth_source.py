"""`auth_source` — how an account proves who it is (local password vs ISAP SSO).

The platform store never recorded provisioning origin, so every pre-existing row
is backfilled to "local" (scripts/init_db.py) and only accounts created by SSO
from here on are marked "sso".
"""

import pytest
from fastapi import HTTPException

from app.api.v1.auth import create_user, reset_user_password, update_user
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.auth import PasswordReset, UserCreate, UserUpdate
from app.services.auth_service import authenticate_user
from app.services.sso_service import resolve_sso_user

PASSWORD = "pw12345"


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return []


class _FakeDB:
    def __init__(self, user=None):
        self._user = user
        self.added = []
        self.committed = False

    def query(self, _model):
        return _FakeQuery(self._user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _admin():
    return User(id=1, username="root", is_superuser=True, is_active=True)


# ── default / model ─────────────────────────────────────────────────────────


def test_default_is_local():
    """Anything created without saying otherwise is a local account, and so is
    every row that predates the column (server_default fills it in place)."""
    column = User.__table__.c.auth_source
    assert column.default.arg == "local"
    assert column.server_default.arg == "local"
    assert not column.nullable

    created = create_user(UserCreate(username="u", password=PASSWORD), db=_FakeDB(), _admin=_admin())
    assert created.auth_source == "local"


# ── password login policy ───────────────────────────────────────────────────


def test_sso_account_cannot_log_in_with_a_password():
    """Otherwise the flag is decoration: one admin password reset would quietly
    re-open the route that bypasses isap-auth (and its MFA / revocation)."""
    user = User(
        username="A004077",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        auth_source="sso",
    )
    assert authenticate_user(_FakeDB(user), "A004077", PASSWORD) is None


def test_local_account_still_logs_in():
    user = User(username="admin", hashed_password=get_password_hash(PASSWORD), is_active=True, auth_source="local")
    assert authenticate_user(_FakeDB(user), "admin", PASSWORD) is user


def test_backfilled_account_still_logs_in():
    """A row written before this column existed reads back as "local"."""
    user = User(username="legacy", hashed_password=get_password_hash(PASSWORD), is_active=True)
    user.auth_source = "local"  # what the server_default gives an existing row
    assert authenticate_user(_FakeDB(user), "legacy", PASSWORD) is user


# ── SSO provisioning ────────────────────────────────────────────────────────


def test_sso_autocreated_user_is_marked_sso(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "sso_autocreate", True)
    db = _FakeDB(user=None)
    user = resolve_sso_user(db, "A000150")
    assert user.auth_source == "sso"


def test_sso_login_still_works_for_a_local_account():
    """auth_source gates the PASSWORD route only. Gating the SSO side would lock
    out every backfilled user, since they are all "local"."""
    existing = User(username="A004077", is_active=True, is_service_account=False, auth_source="local")
    assert resolve_sso_user(_FakeDB(existing), "A004077") is existing


# ── creation ────────────────────────────────────────────────────────────────


def test_sso_account_can_be_created_without_a_password():
    """Pre-provisioning is the normal path here: SSO_AUTOCREATE is off, so an
    admin creates the account before the user's first SSO login."""
    created = create_user(UserCreate(username="A003968", auth_source="sso"), db=_FakeDB(), _admin=_admin())
    assert created.auth_source == "sso"
    # An unguessable hash, not an empty one: verify("") would raise instead of
    # returning False.
    assert created.hashed_password
    assert not verify_password("", created.hashed_password)


def test_local_account_without_a_password_is_rejected():
    with pytest.raises(HTTPException) as exc:
        create_user(UserCreate(username="u", auth_source="local"), db=_FakeDB(), _admin=_admin())
    assert exc.value.status_code == 400
    assert "password" in exc.value.detail.lower()


def test_service_account_cannot_be_sso_on_create():
    with pytest.raises(HTTPException) as exc:
        create_user(
            UserCreate(username="svc", password=PASSWORD, is_service_account=True, auth_source="sso"),
            db=_FakeDB(),
            _admin=_admin(),
        )
    assert exc.value.status_code == 400


def test_auth_source_is_constrained_to_known_values():
    with pytest.raises(Exception):
        UserCreate(username="u", password=PASSWORD, auth_source="ldap")


# ── update ──────────────────────────────────────────────────────────────────


def test_update_can_switch_an_account_to_sso():
    user = User(id=2, username="alice", is_active=True, auth_source="local")
    updated = update_user(2, UserUpdate(auth_source="sso"), db=_FakeDB(user), admin=_admin())
    assert updated.auth_source == "sso"


def test_update_rejects_service_account_plus_sso():
    """Checked against the RESULTING state: flipping either half into conflict
    must fail, not just sending both at once."""
    user = User(id=2, username="svc", is_active=True, is_service_account=True, auth_source="local")
    with pytest.raises(HTTPException) as exc:
        update_user(2, UserUpdate(auth_source="sso"), db=_FakeDB(user), admin=_admin())
    assert exc.value.status_code == 400


def test_update_leaves_auth_source_alone_when_omitted():
    user = User(id=2, username="alice", is_active=True, auth_source="sso")
    updated = update_user(2, UserUpdate(full_name="Alice"), db=_FakeDB(user), admin=_admin())
    assert updated.auth_source == "sso"


# ── password reset ──────────────────────────────────────────────────────────


def test_password_reset_refused_on_an_sso_account():
    """Accepting it would leave the admin believing they had handed out working
    credentials, when password login is blocked for this account."""
    user = User(id=2, username="A004077", is_active=True, auth_source="sso", hashed_password="x")
    with pytest.raises(HTTPException) as exc:
        reset_user_password(2, PasswordReset(new_password="newpw123"), db=_FakeDB(user), _admin=_admin())
    assert exc.value.status_code == 400
    assert user.hashed_password == "x"


def test_password_reset_still_works_on_a_local_account():
    user = User(id=2, username="alice", is_active=True, auth_source="local", hashed_password="x")
    reset_user_password(2, PasswordReset(new_password="newpw123"), db=_FakeDB(user), _admin=_admin())
    assert verify_password("newpw123", user.hashed_password)
