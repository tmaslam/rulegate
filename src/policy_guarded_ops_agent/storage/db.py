"""Database selection: Neon/Postgres live, SQLite offline. Both paths must work.

One decision, made once
-----------------------
:func:`resolve_database` turns ``DATABASE_URL`` (or its absence) into a
:class:`DatabaseConfig`. It is a **pure function of a string** — no connection,
no env read, no I/O — so every branch below is unit-tested offline with no
Postgres anywhere near it (``tests/test_storage.py``). Nothing else in the
codebase parses a database URL.

The two-URL problem
-------------------
The same database needs two *different* URL spellings, and mixing them up is the
bug this module exists to prevent:

* **SQLAlchemy** wants an explicit driver: ``postgresql+psycopg://...`` or
  ``sqlite+aiosqlite:///...``. Without the ``+driver`` it picks a default
  (psycopg2 / the stdlib sqlite3) that is not installed and/or is synchronous,
  and the async engine dies at connect time.
* **LangGraph's** ``AsyncPostgresSaver`` wants a raw **libpq DSN**
  (``postgresql://...``) because it hands the string to psycopg directly.
  Give it SQLAlchemy's ``+psycopg`` form and psycopg fails to parse it.

So :class:`DatabaseConfig` carries both, derived from one input, and each
consumer takes the one it needs. Neither is reconstructed by string-mangling at
the call site.

Neon
----
Neon issues ``postgresql://user:pass@ep-xxx.region.aws.neon.tech/neondb?sslmode=require``.
That is passed through untouched apart from the driver prefix: the query string
carries ``sslmode``/``channel_binding``, and rewriting it is how you end up
silently disabling TLS to a database on the public internet.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import structlog
from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_SQLITE_PATH",
    "DatabaseBackend",
    "DatabaseConfig",
    "MissingPostgresDriverError",
    "resolve_database",
]

log: Final = structlog.get_logger(__name__)

#: Where the offline fallback puts its file. Relative to the working directory so
#: a fresh clone writes inside the repo (gitignored) rather than somewhere it
#: needs permission for.
DEFAULT_SQLITE_PATH: Final = "./data/ops_agent.db"

_POSTGRES_SCHEMES: Final = frozenset({"postgres", "postgresql"})
_SQLITE_SCHEMES: Final = frozenset({"sqlite"})


class DatabaseBackend(StrEnum):
    """Which backend a resolved URL points at."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"


class MissingPostgresDriverError(RuntimeError):
    """``DATABASE_URL`` names Postgres but the ``db`` extra is not installed.

    Raised at connect time with an actionable message rather than surfacing as a
    bare ``ModuleNotFoundError: psycopg`` from three frames down.
    """

    def __init__(self) -> None:
        super().__init__(
            "DATABASE_URL points at Postgres but the Postgres driver is not "
            "installed. Install the optional extra:\n"
            "    uv sync --extra db\n"
            "Or unset DATABASE_URL to use the offline SQLite fallback, which "
            "needs no extras and no accounts."
        )


class DatabaseConfig(BaseModel):
    """A resolved database target.

    Frozen: resolution happens once at startup and is then a fact, not a knob.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: DatabaseBackend
    #: SQLAlchemy async URL, driver explicit. For the audit trail + approvals.
    sqlalchemy_url: str
    #: LangGraph checkpointer target. A libpq DSN for Postgres; a filesystem
    #: path (or ``:memory:``) for SQLite.
    checkpoint_dsn: str
    #: True when this is the zero-account offline default rather than a
    #: deliberately configured database. Surfaced on /health so a deploy that
    #: silently fell back to SQLite is visible instead of looking healthy.
    is_fallback: bool = False

    @property
    def is_postgres(self) -> bool:
        """Whether this config targets Postgres."""
        return self.backend is DatabaseBackend.POSTGRES


def _sqlite_config(path: str, *, is_fallback: bool) -> DatabaseConfig:
    """Build a SQLite config, ensuring the parent directory exists.

    ``:memory:`` is passed through untouched — it has no parent directory, and
    ``mkdir`` on it would create a literal ``:memory:`` folder.
    """
    if path != ":memory:":
        parent = Path(path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        resolved = Path(path).expanduser().resolve().as_posix()
    else:
        resolved = path
    return DatabaseConfig(
        backend=DatabaseBackend.SQLITE,
        # Four slashes for an absolute path: sqlite+aiosqlite:////abs/path.
        # `as_posix()` on Windows yields "C:/..."; SQLAlchemy accepts that after
        # the three-slash prefix.
        sqlalchemy_url=(
            "sqlite+aiosqlite:///:memory:"
            if resolved == ":memory:"
            else f"sqlite+aiosqlite:///{resolved}"
        ),
        checkpoint_dsn=resolved,
        is_fallback=is_fallback,
    )


def _postgres_config(url: str) -> DatabaseConfig:
    """Build a Postgres config, normalising the driver prefix for each consumer."""
    parts = urlsplit(url)
    # Normalise the scheme: `postgres://` is a legacy alias, and any `+driver`
    # suffix the caller supplied is replaced rather than appended to.
    bare_scheme = parts.scheme.split("+", 1)[0]
    libpq_scheme = "postgresql" if bare_scheme in _POSTGRES_SCHEMES else bare_scheme
    # Query string (sslmode, channel_binding) is preserved verbatim — Neon needs
    # it and rewriting it is how TLS gets silently dropped.
    libpq_dsn = urlunsplit(
        (libpq_scheme, parts.netloc, parts.path, parts.query, parts.fragment)
    )
    sqlalchemy_url = urlunsplit(
        (f"{libpq_scheme}+psycopg", parts.netloc, parts.path, parts.query, parts.fragment)
    )
    return DatabaseConfig(
        backend=DatabaseBackend.POSTGRES,
        sqlalchemy_url=sqlalchemy_url,
        checkpoint_dsn=libpq_dsn,
        is_fallback=False,
    )


def resolve_database(url: str | None) -> DatabaseConfig:
    """Resolve ``DATABASE_URL`` to a concrete backend.

    Pure: parses a string and returns a config. The only side effect is creating
    the SQLite parent directory, which must happen before anything tries to open
    the file.

    Args:
        url: The raw ``DATABASE_URL``. ``None``/empty selects the SQLite
            fallback — the supported zero-account default, not an error.

    Returns:
        The resolved config.

    Raises:
        ValueError: The URL names a scheme this service does not support. Better
            to fail at startup than to connect somewhere unintended.
    """
    if url is None or not url.strip():
        log.info("database_fallback", backend="sqlite", path=DEFAULT_SQLITE_PATH)
        return _sqlite_config(DEFAULT_SQLITE_PATH, is_fallback=True)

    raw = url.strip()
    scheme = urlsplit(raw).scheme.lower()
    bare_scheme = scheme.split("+", 1)[0]

    if bare_scheme in _POSTGRES_SCHEMES:
        config = _postgres_config(raw)
        log.info("database_selected", backend="postgres")
        return config

    if bare_scheme in _SQLITE_SCHEMES:
        # Accept sqlite:///path, sqlite+aiosqlite:///path and sqlite:///:memory:.
        path = urlsplit(raw).path.lstrip("/") or ":memory:"
        if raw.endswith(":memory:"):
            path = ":memory:"
        log.info("database_selected", backend="sqlite", path=path)
        return _sqlite_config(path, is_fallback=False)

    msg = (
        f"unsupported DATABASE_URL scheme {scheme!r}. Supported: "
        "postgresql:// (Neon or any Postgres), sqlite:// (offline fallback), "
        "or unset for the SQLite default."
    )
    raise ValueError(msg)
