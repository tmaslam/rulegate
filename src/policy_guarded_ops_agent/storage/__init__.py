"""Persistence: Neon/Postgres when configured, SQLite when not. Both work.

The selection is made once, deterministically, from ``DATABASE_URL`` — see
``db.py::resolve_database``. Nothing downstream branches on "am I in
production"; it branches on the resolved
:class:`~policy_guarded_ops_agent.storage.db.DatabaseConfig`, which is a value
you can construct in a test.
"""

from __future__ import annotations
