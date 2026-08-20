"""The MSSQL pool must honour pool_max — it used to ignore it entirely.

``_acquire`` opened a fresh connection whenever the idle queue was empty, so a
burst of concurrent calls grew the pool without limit and the surplus queued on
the SQL Server instead. These tests pin the ceiling and the refusal.
"""

import threading

import pytest

from app.db.adapters.mssql_adapter import MssqlPoolAdapter
from app.db.multi_pool import DatabaseConfig
from app.db.pool_adapter import PoolExhaustedError


class FakeCursor:
    def execute(self, *_a, **_kw):
        return None

    def close(self):
        return None


class FakeConnection:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def cursor(self):
        return FakeCursor()

    def close(self):
        self.closed = True


def make_adapter(pool_max: int, pool_min: int = 0, acquire_timeout: float = 0.1) -> MssqlPoolAdapter:
    adapter = MssqlPoolAdapter()
    adapter.opened = 0

    def fake_create():
        adapter.opened += 1
        return FakeConnection(adapter)

    adapter._create_connection = fake_create  # type: ignore[method-assign]
    adapter.create(
        DatabaseConfig(
            alias="T",
            db_type="mssql",
            pool_min=pool_min,
            pool_max=pool_max,
            pool_acquire_timeout=acquire_timeout,
        )
    )
    return adapter


def test_never_opens_more_than_pool_max_concurrently():
    adapter = make_adapter(pool_max=3)
    held = [adapter._acquire() for _ in range(3)]
    assert adapter.opened == 3

    with pytest.raises(PoolExhaustedError):
        adapter._acquire()

    for conn in held:
        adapter._release(conn)
    # Released connections are reused, not re-opened.
    adapter._release(adapter._acquire())
    assert adapter.opened == 3


def test_waiter_is_served_when_a_connection_comes_back():
    adapter = make_adapter(pool_max=1, acquire_timeout=5.0)
    first = adapter._acquire()
    got: list = []

    def waiter():
        got.append(adapter._acquire())

    t = threading.Thread(target=waiter)
    t.start()
    adapter._release(first)
    t.join(timeout=5)

    assert not t.is_alive()
    assert got and adapter.opened == 1


def test_failed_connect_does_not_leak_a_slot():
    adapter = make_adapter(pool_max=1)

    def boom():
        raise RuntimeError("login failed")

    adapter._create_connection = boom  # type: ignore[method-assign]
    for _ in range(3):
        with pytest.raises(RuntimeError, match="login failed"):
            adapter._acquire()

    # The ceiling must still be 1, not 0 — a leaked slot would turn every later
    # acquire into a PoolExhaustedError even once the server recovered.
    adapter._create_connection = lambda: FakeConnection(adapter)  # type: ignore[method-assign]
    assert adapter._acquire() is not None


def test_pool_min_is_capped_by_pool_max():
    adapter = make_adapter(pool_max=2, pool_min=5)
    assert adapter.opened == 2
