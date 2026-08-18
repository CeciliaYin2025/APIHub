"""The RFC 9728 metadata route and the 401 challenge that advertises it."""

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.main import oauth_protected_resource
from app.models.user import User
from app.services.auth_service import Principal, get_current_user


class _State:
    def __init__(self, principal):
        self.principal = principal


class _Request:
    def __init__(self, principal):
        self.state = _State(principal)


@pytest.fixture
def _oauth_on(monkeypatch):
    monkeypatch.setattr(settings, "oauth_rs_enabled", True)
    monkeypatch.setattr(settings, "oauth_introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(settings, "oauth_issuer", "https://isap.example.com/auth")
    monkeypatch.setattr(settings, "apihub_public_url", "https://isap.example.com/apihub")


def test_metadata_served_when_enabled(_oauth_on):
    meta = oauth_protected_resource()
    assert meta["resource"] == "https://isap.example.com/apihub"
    assert meta["authorization_servers"] == ["https://isap.example.com/auth"]


def test_metadata_404s_when_disabled(monkeypatch):
    """The route should not exist on a deployment that does not accept OAuth."""
    monkeypatch.setattr(settings, "oauth_rs_enabled", False)
    with pytest.raises(HTTPException) as e:
        oauth_protected_resource()
    assert e.value.status_code == 404


def test_401_advertises_metadata_when_enabled(_oauth_on):
    with pytest.raises(HTTPException) as e:
        get_current_user(_Request(None), None, None)
    assert e.value.status_code == 401
    assert "oauth-protected-resource" in e.value.headers["WWW-Authenticate"]


def test_401_stays_bare_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "oauth_rs_enabled", False)
    with pytest.raises(HTTPException) as e:
        get_current_user(_Request(None), None, None)
    assert e.value.headers["WWW-Authenticate"] == "Bearer"


def test_authenticated_caller_unaffected(_oauth_on):
    user = User(username="svc", is_active=True)
    assert get_current_user(_Request(Principal(type="sso_user", user=user)), None, None) is user
