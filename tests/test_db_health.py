"""Per-database connectivity probing."""

import contextlib
import threading
import time

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


def _result(alias, status="online"):
    return {
        "alias": alias,
        "status": status,
        "latency_ms": 1.0 if status == "online" else None,
        "error": None if status == "online" else "boom",
        "checked_at": "2026-07-15T00:00:00+00:00",
    }


def test_check_all_returns_one_entry_per_alias(monkeypatch):
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["a", "b"])
    monkeypatch.setattr(svc, "check_database", lambda alias: _result(alias))

    results = svc.check_all_databases()

    assert sorted(r["alias"] for r in results) == ["a", "b"]


def test_check_all_with_no_aliases_returns_empty(monkeypatch):
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: [])
    assert svc.check_all_databases() == []


def test_check_all_isolates_failures(monkeypatch):
    def _fake(alias):
        return _result(alias, "offline" if alias == "bad" else "online")

    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["good", "bad"])
    monkeypatch.setattr(svc, "check_database", _fake)

    statuses = {r["alias"]: r["status"] for r in svc.check_all_databases()}

    # One dead database must not poison the others.
    assert statuses == {"good": "online", "bad": "offline"}


def test_check_all_marks_hung_database_timeout(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["slow"])
    monkeypatch.setattr(svc.settings, "db_health_check_timeout", 0.1)
    monkeypatch.setattr(svc, "check_database", lambda alias: release.wait(10))

    try:
        results = svc.check_all_databases()
        assert results[0]["status"] == "timeout"
        assert results[0]["latency_ms"] is None
        assert "exceeded" in results[0]["error"]
    finally:
        # Release the abandoned worker so it does not block interpreter exit.
        release.set()


def test_check_all_shares_one_deadline_across_aliases(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["s1", "s2", "s3"])
    monkeypatch.setattr(svc.settings, "db_health_check_timeout", 0.2)
    monkeypatch.setattr(svc, "check_database", lambda alias: release.wait(10))

    try:
        started = time.perf_counter()
        results = svc.check_all_databases()
        elapsed = time.perf_counter() - started

        assert all(r["status"] == "timeout" for r in results)
        # Three hung databases share ONE 0.2s budget; they must not
        # accumulate to 3 x 0.2s (0.6s). A regression to per-alias timeouts
        # would land at exactly 0.6s, so use 0.45 to leave margin on both
        # sides between the correct (~0.2s) and regressed (~0.6s) outcomes.
        assert elapsed < 0.45
    finally:
        release.set()
