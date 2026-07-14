"""The queryable audit trail: every decision, tool call, policy check, approval.

Append-only by construction — there is no update or delete on this table's
public API. An audit trail you can edit is not an audit trail.
"""

from __future__ import annotations
