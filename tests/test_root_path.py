import importlib

from fastapi.testclient import TestClient

from app.core.config import Settings


def test_root_path_defaults_to_empty():
    s = Settings()
    assert s.root_path == ""


def test_root_path_reads_from_env(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "/apihub")
    s = Settings()
    assert s.root_path == "/apihub"


def _client(monkeypatch, root_path):
    monkeypatch.setenv("ROOT_PATH", root_path)
    # reload modules so the new env is picked up
    import app.core.config as config_mod

    importlib.reload(config_mod)
    import app.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_admin_index_injects_base_path(monkeypatch):
    client = _client(monkeypatch, "/apihub")
    html = client.get("/admin").text
    # placeholder token must be gone, prefixed URLs present
    assert "{{BASE_PATH}}" not in html
    assert "/apihub/admin/js/app.js" in html
    assert 'window.__BASE_PATH__ = "/apihub"' in html


def test_admin_index_empty_base_path_default(monkeypatch):
    client = _client(monkeypatch, "")
    html = client.get("/admin").text
    assert "{{BASE_PATH}}" not in html  # placeholder substituted, not left raw
    assert '"/admin/js/app.js"' in html
    assert 'window.__BASE_PATH__ = ""' in html
