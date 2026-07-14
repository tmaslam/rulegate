"""Approval queue schema and boundary types.

An approval request is the durable half of a paused graph run. It records *what*
was proposed, *why* it needs a human (the rule that fired), and — once resolved
— *who* decided and when.

``thread_id`` is the join key back to the LangGraph checkpoint. Resuming the
right paused run is entirely dependent on it, so it is unique and indexed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from policy_guarded_ops_agent.storage.base import Base, JsonType, as_utc

__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalRequestRow",
    "ApprovalStatus",
]


class ApprovalStatus(StrEnum):
    """Lifecycle of an approval request.

    ``PENDING`` is the only state in which the graph is paused. Both terminal
    states resume it — a rejection is a decision, not an abandonment, and the
    customer still gets an answer.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequestRow(Base):
    """A queued request for human approval."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        # The reviewer's queue view: pending items, oldest first.
        Index("ix_approval_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: LangGraph thread id. The key used to resume the paused run — without it
    #: an approval is an orphan and the run never continues.
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default=ApprovalStatus.PENDING)
    action_type: Mapped[str] = mapped_column(String(32))
    #: The serialised ProposedAction. Re-validated on read, never trusted raw.
    action_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    #: The rule that forced this to a human. Shown in the queue UI.
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale: Mapped[str] = mapped_column(String(2048), default="")
    #: Value at stake, as a decimal string. Stored as text, not float: this is
    #: money, and it is rendered straight into a reviewer's decision.
    value_usd: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who decided. NULL while pending. Non-null is the entire evidentiary basis
    #: for "a human approved this", so it is never defaulted to a system id.
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ApprovalRequest(BaseModel):
    """Validated approval request. The boundary type for the API and the queue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    thread_id: str
    conversation_id: str
    run_id: str
    status: ApprovalStatus
    action_type: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = None
    rationale: str = ""
    value_usd: str | None = None
    customer_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None

    @field_validator("created_at")
    @classmethod
    def _utc_created(cls, value: datetime) -> datetime:
        return as_utc(value)

    @field_validator("resolved_at")
    @classmethod
    def _utc_resolved(cls, value: datetime | None) -> datetime | None:
        return as_utc(value) if value is not None else None

    @property
    def is_pending(self) -> bool:
        """Whether a human still needs to act on this."""
        return self.status is ApprovalStatus.PENDING

    @classmethod
    def from_row(cls, row: ApprovalRequestRow) -> Self:
        """Build the boundary type from a stored row."""
        return cls(
            approval_id=row.approval_id,
            thread_id=row.thread_id,
            conversation_id=row.conversation_id,
            run_id=row.run_id,
            status=ApprovalStatus(row.status),
            action_type=row.action_type,
            action_payload=dict(row.action_payload or {}),
            rule_id=row.rule_id,
            rationale=row.rationale,
            value_usd=row.value_usd,
            customer_id=row.customer_id,
            created_at=as_utc(row.created_at),
            resolved_at=as_utc(row.resolved_at) if row.resolved_at is not None else None,
            resolved_by=row.resolved_by,
            resolution_note=row.resolution_note,
        )


class ApprovalDecision(BaseModel):
    """A human's verdict, as it travels back into the paused graph.

    This is the exact payload handed to LangGraph's ``Command(resume=...)``, so
    it is the value ``interrupt()`` returns inside the node. Keeping it a typed
    model rather than a bare bool means the resumed node can record *who*
    approved without a second lookup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool
    #: Identity of the human. Required — an approval with no name attached is
    #: not an approval, it is an unattributed state change.
    resolved_by: str = Field(min_length=1)
    note: str = ""
