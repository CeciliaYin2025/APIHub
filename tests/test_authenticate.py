"""Tests for authenticate_user: credential check + active-account gate."""

from app.core.security import get_password_hash
from app.models.user import User
from app.services.auth_service import authenticate_user

PASSWORD = "correct-horse"


class _FakeQuery:
    def __init__(self, user):
        self._user = user

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._user


class _FakeDB:
    """Minimal stand-in for a SQLAlchemy Session returning a fixed user."""

    def __init__(self, user):
        self._user = user

    def query(self, _model):
        return _FakeQuery(self._user)


def _user(*, is_active: bool) -> User:
    return User(
        username="alice",
        hashed_password=get_password_hash(PASSWORD),
        is_active=is_active,
    )


def test_active_user_with_correct_password_authenticates():
    user = _user(is_active=True)
    assert authenticate_user(_FakeDB(user), "alice", PASSWORD) is user


def test_wrong_password_is_rejected():
    user = _user(is_active=True)
    assert authenticate_user(_FakeDB(user), "alice", "wrong") is None


def test_unknown_user_is_rejected():
    assert authenticate_user(_FakeDB(None), "ghost", PASSWORD) is None


def test_inactive_user_is_rejected_even_with_correct_password():
    user = _user(is_active=False)
    assert authenticate_user(_FakeDB(user), "alice", PASSWORD) is None
