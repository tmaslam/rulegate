"""The declarative base and shared column types for every table.

Lives in ``storage/`` rather than in ``audit/`` or ``approvals/`` because the
metadata is shared: ``Base.metadata.create_all`` must see *every* table, and a
base defined inside one feature package would make the other's tables invisible
to it depending on import order — a bug that shows up as a missing table at
runtime and nowhere at import time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "JsonType", "as_utc"]


class Base(DeclarativeBase):
    """Declarative base for every table in this service."""


#: JSON on SQLite, JSONB on Postgres. `with_variant` picks per-dialect at DDL
#: time, so the live path gets indexable binary JSON while the offline fallback
#: still works. `sqlalchemy.dialects.postgresql` is part of SQLAlchemy core — it
#: imports fine with no psycopg installed, so this costs the fallback nothing.
JsonType: Final = JSON().with_variant(JSONB(), "postgresql")


def as_utc(value: datetime) -> datetime:
    """Re-attach UTC to a datetime read back from a backend that dropped it.

    ``DateTime(timezone=True)`` is honoured by Postgres and **silently ignored
    by SQLite**, which has no timestamp type and returns a naive datetime. Since
    this service must behave identically on Neon and on the SQLite fallback, a
    naive value on one and an aware value on the other would make ``created_at``
    comparisons wrong on exactly one backend — the classic "passes locally,
    wrong in production" bug.

    Everything is written as UTC, so a naive value read back *is* UTC. Labelling
    it restores known information; it does not guess.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
