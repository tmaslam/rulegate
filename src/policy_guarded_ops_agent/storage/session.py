"""Async engine and session lifecycle for both backends.

Schema management
-----------------
:meth:`Database.create_all` is deliberately used instead of Alembic. This is a
demo repo whose schema ships in one commit; wiring migrations would be
ceremony that proves nothing. **That is a real limitation and it is stated in
the README rather than papered over** — a service that will outlive its first
schema change needs Alembic, and this one would get it on day two.

Driver import
-------------
SQLAlchemy imports the DBAPI driver when the engine is *created*, not when it
first connects. So a missing ``psycopg`` surfaces here, at startup, and is
translated into :class:`~.db.MissingPostgresDriverError` — an error that tells
you which command to run — instead of a bare ``ModuleNotFoundError`` from deep
inside SQLAlchemy's dialect loader.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from policy_guarded_ops_agent.storage.base import Base
from policy_guarded_ops_agent.storage.db import DatabaseConfig, MissingPostgresDriverError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["Database"]

log: Final = structlog.get_logger(__name__)


class Database:
    """Owns the async engine and hands out sessions.

    One instance per process, created in the FastAPI lifespan and disposed on
    shutdown. Holding it as a singleton matters: connection pools are expensive,
    and on Neon's free tier the connection ceiling is low enough that a
    per-request engine would exhaust it under trivial load.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._engine = self._create_engine(config)
        self._sessionmaker = async_sessionmaker(
            self._engine,
            expire_on_commit=False,  # rows stay usable after commit
            class_=AsyncSession,
        )

    @staticmethod
    def _create_engine(config: DatabaseConfig) -> AsyncEngine:
        """Create the async engine, translating a missing driver into a fix."""
        kwargs: dict[str, Any] = {"echo": False, "future": True}
        if config.is_postgres:
            # pool_pre_ping: Neon's free tier suspends an idle compute after a few
            # minutes, which silently kills pooled connections. Without pre-ping
            # the first request after an idle period fails with a stale-connection
            # error that looks like a bug in this service and is not.
            kwargs["pool_pre_ping"] = True
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 5
        try:
            return create_async_engine(config.sqlalchemy_url, **kwargs)
        except ModuleNotFoundError as exc:
            if config.is_postgres:
                raise MissingPostgresDriverError from exc
            raise

    @property
    def config(self) -> DatabaseConfig:
        """The resolved config this database was built from."""
        return self._config

    async def create_all(self) -> None:
        """Create every table if absent. Idempotent.

        See the module docstring on why this is not Alembic.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info(
            "schema_ready",
            backend=str(self._config.backend),
            tables=sorted(Base.metadata.tables),
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, committing on success and rolling back on error.

        The rollback is not optional: an exception mid-write leaves the session
        in a failed state, and returning it to the pool dirty makes the *next*
        unrelated request fail with a confusing InvalidRequestError.
        """
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close the pool. Call on shutdown."""
        await self._engine.dispose()
        log.info("database_disposed", backend=str(self._config.backend))
