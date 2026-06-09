from app.core.config import Settings


def test_root_path_defaults_to_empty():
    s = Settings()
    assert s.root_path == ""


def test_root_path_reads_from_env(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "/apihub")
    s = Settings()
    assert s.root_path == "/apihub"
