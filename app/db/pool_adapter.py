"""Abstract pool adapter interface for database connection pooling."""

from __future__ import annotations

import abc
from contextlib import contextmanager
from typing import Any, Generator


class PoolExhaustedError(RuntimeError):
    """No connection became free within the pool's acquire timeout.

    Raised instead of queueing indefinitely: a caller that waits forever holds
    on to a worker thread, so a flood of slow queries against one alias starves
    every other endpoint in the process — including login. Surfaces as HTTP 503.
    """


class PoolAdapter(abc.ABC):
    """Engine-agnostic connection pool wrapper."""

    @abc.abstractmethod
    def create(self, config: Any) -> None:
        """Create the underlying connection pool from a DatabaseConfig."""

    @abc.abstractmethod
    def close(self, force: bool = False) -> None:
        """Close the pool and release all connections."""

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        """Acquire a connection, yield it, then release it back to the pool."""
        conn = self._acquire()
        try:
            yield conn
        finally:
            self._release(conn)

    @abc.abstractmethod
    def _acquire(self) -> Any:
        """Acquire a connection from the pool."""

    @abc.abstractmethod
    def _release(self, conn: Any) -> None:
        """Release a connection back to the pool."""
