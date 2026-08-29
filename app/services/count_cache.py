"""Short-TTL cache for the COUNT(*) that `page` and `batch` fetch modes run.

Both modes issue two statements per request: the count, and the page itself.
Walking a definition therefore recomputes the *same* count once per page — for an
unfiltered ``eo_detail`` (45k rows, MSSQL) that count was ~1.8s of a ~2.8s request,
so a 450-page walk spent roughly 13 minutes of DB time re-answering a question
whose answer had not changed.

The cache is deliberately modest:

- **Process-local**, like the rate limiter. Multiple API instances each keep their
  own; there is nothing to invalidate across them.
- **Short TTL** (``QUERY_COUNT_CACHE_TTL_SECONDS``, default 60s). A paging walk is
  the thing being optimised and it finishes well inside that window.
- **Only `page` and `batch`.** ``fetch_mode="count"`` asks for the count as the
  answer, so it always reads live.

Consequence to be aware of: `batch`'s ``has_more`` is derived from ``total``, so a
count cached at the start of a walk is what bounds that walk. This does not make
paging less consistent than it already was — each page has always been a separate
query against possibly-changed data — but it does mean rows inserted mid-walk stay
invisible until the entry expires.
"""

import threading
import time
from collections import OrderedDict
from typing import Any

from app.core.config import settings

# key -> (expires_at, count). OrderedDict keeps insertion order so eviction can
# drop the oldest entry without scanning.
_entries: "OrderedDict[tuple, tuple[float, int]]" = OrderedDict()
_lock = threading.Lock()


def _make_key(db_alias: str | None, count_sql: str, binds: dict[str, Any]) -> tuple:
    """Build a hashable key from the alias, the count SQL and the bind values.

    Bind values are stringified rather than used raw: they arrive from JSON and
    from the DB-backed registry, so they may be unhashable (list) or compare
    equal across types (1 vs True). ``repr`` keeps those distinct.
    """
    bind_repr = tuple(sorted((name, repr(value)) for name, value in binds.items()))
    return (db_alias or "", count_sql, bind_repr)


def get(db_alias: str | None, count_sql: str, binds: dict[str, Any]) -> int | None:
    """Return the cached count, or None on a miss/expiry/disabled cache."""
    ttl = settings.query_count_cache_ttl_seconds
    if ttl <= 0:
        return None

    key = _make_key(db_alias, count_sql, binds)
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return None
        expires_at, count = entry
        if expires_at <= now:
            del _entries[key]
            return None
        return count


def put(db_alias: str | None, count_sql: str, binds: dict[str, Any], count: int) -> None:
    """Store a count. No-op when the cache is disabled."""
    ttl = settings.query_count_cache_ttl_seconds
    if ttl <= 0:
        return

    key = _make_key(db_alias, count_sql, binds)
    now = time.monotonic()
    with _lock:
        # Drop anything already expired before measuring size, so a burst of
        # distinct binds does not evict live entries while dead ones sit there.
        for stale_key in [k for k, (exp, _) in _entries.items() if exp <= now]:
            del _entries[stale_key]

        _entries.pop(key, None)
        _entries[key] = (now + ttl, count)

        max_entries = settings.query_count_cache_max_entries
        while max_entries > 0 and len(_entries) > max_entries:
            _entries.popitem(last=False)


def clear() -> None:
    """Drop every entry. For tests and for a definition-CRUD refresh."""
    with _lock:
        _entries.clear()
