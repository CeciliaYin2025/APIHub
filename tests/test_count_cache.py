"""The COUNT(*) memo used by the page/batch fetch modes."""

import pytest

from app.core.config import settings
from app.services import count_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    count_cache.clear()
    yield
    count_cache.clear()


def test_miss_then_hit():
    assert count_cache.get("DW", "SELECT COUNT(*)", {"ou": "SP"}) is None
    count_cache.put("DW", "SELECT COUNT(*)", {"ou": "SP"}, 45014)
    assert count_cache.get("DW", "SELECT COUNT(*)", {"ou": "SP"}) == 45014


def test_binds_are_part_of_the_key():
    count_cache.put("DW", "SELECT COUNT(*)", {"ou": "SP"}, 45014)
    assert count_cache.get("DW", "SELECT COUNT(*)", {"ou": "SC"}) is None


def test_bind_order_does_not_matter():
    count_cache.put("DW", "SQL", {"ou": "SP", "pid": "DMN"}, 544)
    assert count_cache.get("DW", "SQL", {"pid": "DMN", "ou": "SP"}) == 544


def test_alias_is_part_of_the_key():
    """Same definition name/SQL can exist under two aliases with different data."""
    count_cache.put("DW", "SQL", {}, 1)
    assert count_cache.get("HRDC", "SQL", {}) is None


def test_sql_is_part_of_the_key():
    """An edited definition changes the SQL, so it cannot read a stale count."""
    count_cache.put("DW", "SELECT COUNT(*) FROM (SELECT a) c", {}, 10)
    assert count_cache.get("DW", "SELECT COUNT(*) FROM (SELECT b) c", {}) is None


def test_unhashable_bind_value_does_not_explode():
    count_cache.put("DW", "SQL", {"ous": ["SP", "SC"]}, 7)
    assert count_cache.get("DW", "SQL", {"ous": ["SP", "SC"]}) == 7
    assert count_cache.get("DW", "SQL", {"ous": ["SP"]}) is None


def test_int_and_bool_binds_stay_distinct():
    count_cache.put("DW", "SQL", {"flag": 1}, 11)
    assert count_cache.get("DW", "SQL", {"flag": True}) is None


def test_expiry(monkeypatch):
    monkeypatch.setattr(settings, "query_count_cache_ttl_seconds", 30)
    clock = {"now": 1000.0}
    monkeypatch.setattr(count_cache.time, "monotonic", lambda: clock["now"])

    count_cache.put("DW", "SQL", {}, 42)
    clock["now"] = 1029.0
    assert count_cache.get("DW", "SQL", {}) == 42
    clock["now"] = 1031.0
    assert count_cache.get("DW", "SQL", {}) is None


def test_ttl_zero_disables(monkeypatch):
    monkeypatch.setattr(settings, "query_count_cache_ttl_seconds", 0)
    count_cache.put("DW", "SQL", {}, 42)
    assert count_cache.get("DW", "SQL", {}) is None


def test_max_entries_evicts_oldest(monkeypatch):
    monkeypatch.setattr(settings, "query_count_cache_max_entries", 3)
    for i in range(4):
        count_cache.put("DW", "SQL", {"p": i}, i)

    assert count_cache.get("DW", "SQL", {"p": 0}) is None  # evicted
    for i in (1, 2, 3):
        assert count_cache.get("DW", "SQL", {"p": i}) == i


def test_expired_entries_are_reaped_before_eviction(monkeypatch):
    """A dead entry must not cost a live one its slot."""
    monkeypatch.setattr(settings, "query_count_cache_ttl_seconds", 30)
    monkeypatch.setattr(settings, "query_count_cache_max_entries", 2)
    clock = {"now": 1000.0}
    monkeypatch.setattr(count_cache.time, "monotonic", lambda: clock["now"])

    count_cache.put("DW", "SQL", {"p": "old"}, 1)
    clock["now"] = 1040.0  # "old" is now expired
    count_cache.put("DW", "SQL", {"p": "a"}, 2)
    count_cache.put("DW", "SQL", {"p": "b"}, 3)

    assert count_cache.get("DW", "SQL", {"p": "a"}) == 2
    assert count_cache.get("DW", "SQL", {"p": "b"}) == 3
