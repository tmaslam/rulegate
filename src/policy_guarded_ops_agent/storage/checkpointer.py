"""LangGraph checkpointer selection: AsyncPostgresSaver live, AsyncSqliteSaver offline.

This is what makes ``interrupt()`` survive a restart. When the graph pauses on a
high-value refund, the entire graph state is written to the checkpointer. The
process can then die, be redeployed, or scale to zero on a free tier — and when
the human finally clicks approve, the run resumes from exactly where it stopped.
Without a durable checkpointer, ``interrupt()`` is just a coroutine parked in
RAM, and a restart silently strands the customer's refund forever.

Async savers, both arms
-----------------------
The sync ``SqliteSaver`` would block the event loop on every checkpoint write,
inside a FastAPI request. Both arms here are async.

Both savers are async **context managers** that own their connection, so this
module exposes a context manager too rather than a factory returning a bare
object. The connection's lifetime is the service's lifetime, tied to the FastAPI
lifespan (``api/app.py``). Returning a saver whose connection had already been
closed by an exited ``with`` block is the subtle failure this shape prevents.

The Postgres import is lazy — it lives in the optional ``db`` extra, and the
offline path must not require it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import structlog

from policy_guarded_ops_agent.storage.db import DatabaseConfig, MissingPostgresDriverError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.base import BaseCheckpointSaver

__all__ = ["build_checkpointer"]

log: Final = structlog.get_logger(__name__)


@asynccontextmanager
async def _postgres_checkpointer(dsn: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """Open an AsyncPostgresSaver against ``dsn`` and ensure its schema exists."""
    try:
        # Lazy import (PLC0415 waived deliberately): AsyncPostgresSaver lives in
        # the optional `db` extra. Importing at module scope would make Postgres a
        # hard dependency and break the zero-account offline path, which is the
        # whole point of this module.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: PLC0415
    except ImportError as exc:
        raise MissingPostgresDriverError from exc

    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        # Creates the checkpoint tables if absent. Idempotent, and required
        # before the first write — without it the first interrupt fails with an
        # undefined-table error at the least convenient moment.
        await saver.setup()
        log.info("checkpointer_ready", backend="postgres")
        yield saver


@asynccontextmanager
async def _sqlite_checkpointer(path: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """Open an AsyncSqliteSaver against ``path`` (or ``:memory:``)."""
    # In the core deps, not an extra: this is the default path and must always work.
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: PLC0415

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        await saver.setup()
        log.info("checkpointer_ready", backend="sqlite", path=path)
        yield saver


@asynccontextmanager
async def build_checkpointer(config: DatabaseConfig) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """Yield the checkpointer for ``config``'s backend.

    Selection is by resolved backend, never by an "is this production?" guess.

    Example::

        async with build_checkpointer(config) as saver:
            graph = build_graph(deps).compile(checkpointer=saver)

    Raises:
        MissingPostgresDriverError: Postgres is configured but the ``db`` extra
            is not installed.
    """
    if config.is_postgres:
        async with _postgres_checkpointer(config.checkpoint_dsn) as saver:
            yield saver
    else:
        async with _sqlite_checkpointer(config.checkpoint_dsn) as saver:
            yield saver
