"""Swagger's ISAP login: offered only when the API can actually accept it."""

import app.main as main_mod
import app.services.auth_service as auth_service
from fastapi.security import OAuth2AuthorizationCodeBearer

from app.main import _swagger_oauth_config
from app.services.auth_service import build_isap_oauth_scheme

# Patch each module's OWN settings binding rather than a from-imported one:
# tests/test_root_path.py reloads app.core.config and app.main, after which
# app.main holds a different Settings instance than a plain
# `from app.core.config import settings` in this file would.


def _enable(monkeypatch, module):
    monkeypatch.setattr(module.settings, "oauth_rs_enabled", True)
    monkeypatch.setattr(module.settings, "isap_auth_public_url", "https://isap.example.com/auth/")
    monkeypatch.setattr(module.settings, "isap_app_id", "apihub")


def test_scheme_uses_browser_facing_urls(monkeypatch):
    """The flow runs in the reader's browser, so the internal docker URL would
    simply not resolve."""
    _enable(monkeypatch, auth_service)
    scheme = build_isap_oauth_scheme()

    assert isinstance(scheme, OAuth2AuthorizationCodeBearer)
    flow = scheme.model.flows.authorizationCode
    assert flow.authorizationUrl == "https://isap.example.com/auth/api/v1/authorize"
    assert flow.tokenUrl == "https://isap.example.com/auth/api/v1/token"


def test_scheme_absent_when_the_api_would_reject_the_token(monkeypatch):
    """Offering the login while OAUTH_RS_ENABLED is off would walk the user
    through a real ISAP sign-in and then 401 every request they make."""
    monkeypatch.setattr(auth_service.settings, "oauth_rs_enabled", False)
    monkeypatch.setattr(auth_service.settings, "isap_auth_public_url", "https://isap.example.com/auth")
    assert not isinstance(build_isap_oauth_scheme(), OAuth2AuthorizationCodeBearer)


def test_scheme_absent_without_an_issuer(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "oauth_rs_enabled", True)
    monkeypatch.setattr(auth_service.settings, "isap_auth_public_url", "")
    assert not isinstance(build_isap_oauth_scheme(), OAuth2AuthorizationCodeBearer)


def test_swagger_config_forces_pkce(monkeypatch):
    """isap-auth registers public clients only — no secret to authenticate
    with, so PKCE is the only thing binding the code to this browser."""
    _enable(monkeypatch, main_mod)
    config = _swagger_oauth_config()
    assert config["usePkceWithAuthorizationCodeGrant"] is True
    assert config["clientId"] == "apihub"


def test_swagger_config_none_when_disabled(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "oauth_rs_enabled", False)
    assert _swagger_oauth_config() is None
