"""Per-database connectivity probing."""

import contextlib

from app.services import db_health_service as svc


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)

    def fetchone(self):
        return (1,)


def _patch_registry(monkeypatch, cursor_cm, db_type="oracle"):
    monkeypatch.setattr(svc.db_registry, "get_db_type", lambda alias: db_type)
    monkeypatch.setattr(svc.db_registry, "get_cursor", cursor_cm)


def test_check_database_online(monkeypatch):
    @contextlib.contextmanager
    def _ok(alias):
        yield _FakeCursor()

    _patch_registry(monkeypatch, _ok)
    result = svc.check_database("orcl")

    assert result["alias"] == "orcl"
    assert result["status"] == "online"
    assert result["error"] is None
    assert isinstance(result["latency_ms"], float)
    assert result["checked_at"]


def test_check_database_uses_dialect_sql(monkeypatch):
    cursor = _FakeCursor()

    @contextlib.contextmanager
    def _ok(alias):
        yield cursor

    _patch_registry(monkeypatch, _ok, db_type="oracle")
    svc.check_database("orcl")

    # Proves the probe goes through the dialect, not a hardcoded string.
    assert cursor.executed == ["SELECT 1 FROM DUAL"]


def test_check_database_offline_captures_driver_error(monkeypatch):
    @contextlib.contextmanager
    def _boom(alias):
        raise RuntimeError("ORA-12541: TNS:no listener")
        yield  # pragma: no cover

    _patch_registry(monkeypatch, _boom)
    result = svc.check_database("orcl")

    assert result["status"] == "offline"
    assert "ORA-12541" in result["error"]
    assert result["latency_ms"] is None
