"""The durable approval queue.

Resolution is a **conditional update**, not a read-modify-write. Two reviewers
clicking "approve" on the same item at the same moment is the expected case in
any real queue, and the naive `SELECT` -> check -> `UPDATE` races: both read
PENDING, both write, and the run resumes twice — issuing the refund twice.

:meth:`ApprovalQueue.resolve` instead issues a single
``UPDATE ... WHERE status = 'pending'`` and inspects ``rowcount``. The database
decides the winner. The loser gets
:class:`AlreadyResolvedError` and no side effect. This works identically on
Postgres and SQLite because it relies only on the atomicity of a single
statement, not on any dialect-specific locking.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final, cast

import structlog
from sqlalchemy import CursorResult, select, update

from policy_guarded_ops_agent.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestRow,
    ApprovalStatus,
)

if TYPE_CHECKING:
    from datetime import datetime

    from policy_guarded_ops_agent.storage.session import Database

__all__ = ["AlreadyResolvedError", "ApprovalNotFoundError", "ApprovalQueue"]

log: Final = structlog.get_logger(__name__)


class ApprovalNotFoundError(LookupError):
    """No approval request with that id."""

    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(f"approval request not found: {approval_id}")


class AlreadyResolvedError(RuntimeError):
    """The request was already approved or rejected.

    Raised when the conditional update matched no rows — i.e. someone else got
    there first. The caller must NOT resume the graph: the winner already did.
    """

    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(
            f"approval request {approval_id} has already been resolved; "
            "the run it gated was resumed by whoever resolved it first"
        )


class ApprovalQueue:
    """Durable queue of actions awaiting a human."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def enqueue(
        self,
        *,
        thread_id: str,
        conversation_id: str,
        run_id: str,
        action_type: str,
        action_payload: dict[str, Any],
        created_at: datetime,
        rule_id: str | None = None,
        rationale: str = "",
        value_usd: str | None = None,
        customer_id: str | None = None,
    ) -> ApprovalRequest:
        """Queue an action for review and return the pending request."""
        request = ApprovalRequest(
            approval_id=uuid.uuid4().hex,
            thread_id=thread_id,
            conversation_id=conversation_id,
            run_id=run_id,
            status=ApprovalStatus.PENDING,
            action_type=action_type,
            action_payload=action_payload,
            rule_id=rule_id,
            rationale=rationale,
            value_usd=value_usd,
            customer_id=customer_id,
            created_at=created_at,
        )
        async with self._db.session() as session:
            session.add(
                ApprovalRequestRow(
                    approval_id=request.approval_id,
                    thread_id=request.thread_id,
                    conversation_id=request.conversation_id,
                    run_id=request.run_id,
                    status=str(request.status),
                    action_type=request.action_type,
                    action_payload=request.action_payload,
                    rule_id=request.rule_id,
                    rationale=request.rationale,
                    value_usd=request.value_usd,
                    customer_id=request.customer_id,
                    created_at=request.created_at,
                )
            )
        log.info(
            "approval_enqueued",
            approval_id=request.approval_id,
            thread_id=thread_id,
            rule_id=rule_id,
        )
        return request

    async def get(self, approval_id: str) -> ApprovalRequest:
        """Fetch one request.

        Raises:
            ApprovalNotFoundError: No such request.
        """
        async with self._db.session() as session:
            row = await session.scalar(
                select(ApprovalRequestRow).where(ApprovalRequestRow.approval_id == approval_id)
            )
            if row is None:
                raise ApprovalNotFoundError(approval_id)
            return ApprovalRequest.from_row(row)

    async def get_by_thread(self, thread_id: str) -> ApprovalRequest | None:
        """Fetch the request gating a given graph thread, if any."""
        async with self._db.session() as session:
            row = await session.scalar(
                select(ApprovalRequestRow).where(ApprovalRequestRow.thread_id == thread_id)
            )
            return ApprovalRequest.from_row(row) if row is not None else None

    async def list_pending(self, *, limit: int = 50) -> tuple[ApprovalRequest, ...]:
        """Pending requests, oldest first — the reviewer's work queue."""
        async with self._db.session() as session:
            result = await session.execute(
                select(ApprovalRequestRow)
                .where(ApprovalRequestRow.status == str(ApprovalStatus.PENDING))
                .order_by(ApprovalRequestRow.created_at)
                .limit(limit)
            )
            return tuple(ApprovalRequest.from_row(row) for row in result.scalars())

    async def resolve(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        resolved_at: datetime,
    ) -> ApprovalRequest:
        """Approve or reject a pending request, atomically.

        Wins or loses a race outright — see the module docstring. A caller that
        receives :class:`AlreadyResolvedError` must not resume the graph.

        Raises:
            ApprovalNotFoundError: No such request.
            AlreadyResolvedError: Someone else resolved it first.
        """
        status = ApprovalStatus.APPROVED if decision.approved else ApprovalStatus.REJECTED
        async with self._db.session() as session:
            # Single atomic statement: the WHERE clause is the lock. Whoever's
            # UPDATE matches the row wins; everyone else matches zero rows.
            #
            # `session.execute()` is typed as returning Result[Any], but a DML
            # statement always yields a CursorResult, which is what carries
            # `rowcount`. The cast documents that invariant rather than hiding it
            # behind a `type: ignore`.
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(ApprovalRequestRow)
                    .where(ApprovalRequestRow.approval_id == approval_id)
                    .where(ApprovalRequestRow.status == str(ApprovalStatus.PENDING))
                    .values(
                        status=str(status),
                        resolved_at=resolved_at,
                        resolved_by=decision.resolved_by,
                        resolution_note=decision.note,
                    )
                ),
            )
            if result.rowcount == 0:
                # Zero rows matched: either it does not exist, or it was already
                # resolved. Distinguish, because they mean very different things
                # to the caller.
                exists = await session.scalar(
                    select(ApprovalRequestRow.approval_id).where(
                        ApprovalRequestRow.approval_id == approval_id
                    )
                )
                if exists is None:
                    raise ApprovalNotFoundError(approval_id)
                raise AlreadyResolvedError(approval_id)

            row = await session.scalar(
                select(ApprovalRequestRow).where(ApprovalRequestRow.approval_id == approval_id)
            )
            if row is None:  # pragma: no cover — just updated it in this transaction.
                raise ApprovalNotFoundError(approval_id)
            resolved = ApprovalRequest.from_row(row)

        log.info(
            "approval_resolved",
            approval_id=approval_id,
            status=str(status),
            resolved_by=decision.resolved_by,
        )
        return resolved
