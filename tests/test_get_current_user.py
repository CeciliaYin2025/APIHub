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
