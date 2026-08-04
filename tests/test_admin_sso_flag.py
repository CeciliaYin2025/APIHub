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
