"""Shared pytest fixtures."""

import pytest

from app.services import count_cache


@pytest.fixture(autouse=True)
def _reset_count_cache():
    """Keep the process-local COUNT(*) memo from leaking between tests.

    query_service caches counts for the page/batch modes, so without this a test
    that pages twice with the same binds would silently assert against a cached
    total from an earlier test.
    """
    count_cache.clear()
    yield
    count_cache.clear()
