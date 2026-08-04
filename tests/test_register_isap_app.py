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
