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
