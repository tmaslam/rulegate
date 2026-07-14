"""Audit trail schema (SQLAlchemy) and its validated boundary type (Pydantic).

Two representations, on purpose: the ORM row is how it is stored and queried,
the Pydantic model is what crosses every boundary (API, tests, the graph). The
translation happens in exactly one place — :meth:`AuditEvent.from_row` /
:meth:`AuditEvent.to_row` — so the two cannot drift silently.

Timestamps are normalised through ``storage.base.as_utc`` on read; see that
function for why SQLite makes this necessary.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from policy_guarded_ops_agent.storage.base import Base, JsonType, as_utc

__all__ = [
    "Actor",
    "AuditEvent",
    "AuditEventRow",
    "AuditEventType",
]


class AuditEventType(StrEnum):
    """What happened. Stable strings — dashboards and tests group by these."""

    RUN_STARTED = "run_started"
    #: An input guardrail refused the request before any model call.
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    #: The LLM proposed an action.
    PROPOSAL = "proposal"
    #: The policy engine reached a verdict (or was bypassed).
    POLICY_DECISION = "policy_decision"
    #: An action needing human approval was queued and the graph paused.
    APPROVAL_REQUESTED = "approval_requested"
    #: A human approved or rejected it.
    APPROVAL_RESOLVED = "approval_resolved"
    #: A tool actually executed against the billing API.
    TOOL_CALL = "tool_call"
    #: A rule was broken by an executed action. The ablation's unit.
    VIOLATION = "violation"
    RUN_COMPLETED = "run_completed"
    ERROR = "error"


class Actor(StrEnum):
    """Who caused the event. Answers "was this a human or the model?"."""

    SYSTEM = "system"
    LLM = "llm"
    POLICY = "policy"
    HUMAN = "human"
    TOOL = "tool"


class AuditEventRow(Base):
    """One append-only row in the audit trail."""

    __tablename__ = "audit_events"
    __table_args__ = (
        # The three questions this table is actually asked:
        #   "show me this conversation"      -> (conversation_id, seq)
        #   "how often does this rule fire?" -> (rule_id)
        #   "violations, guard on vs off"    -> (event_type, policy_enabled)
        Index("ix_audit_conversation_seq", "conversation_id", "seq"),
        Index("ix_audit_rule", "rule_id"),
        Index("ix_audit_type_policy", "event_type", "policy_enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Monotonic position within a run. Wall-clock ties are common at this
    #: granularity, so ordering by `created_at` alone can scramble a trail.
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str] = mapped_column(String(1024), default="")
    action_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The rule that fired. NULL when the event is not a rule verdict. This is
    #: the column that answers "which rule stopped my refund".
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Whether the guard was ON for the run that produced this event. The
    #: ablation is a GROUP BY over this column, which is why it is stored on
    #: every event rather than inferred later.
    policy_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class AuditEvent(BaseModel):
    """Validated audit event. The boundary type — API, graph and tests use this."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    conversation_id: str
    run_id: str
    seq: int
    event_type: AuditEventType
    actor: Actor
    created_at: datetime
    summary: str = ""
    action_type: str | None = None
    rule_id: str | None = None
    effect: str | None = None
    policy_enabled: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @classmethod
    def from_row(cls, row: AuditEventRow) -> Self:
        """Build the boundary type from a stored row. The only ORM->API mapping."""
        return cls(
            event_id=row.event_id,
            conversation_id=row.conversation_id,
            run_id=row.run_id,
            seq=row.seq,
            event_type=AuditEventType(row.event_type),
            actor=Actor(row.actor),
            created_at=as_utc(row.created_at),
            summary=row.summary,
            action_type=row.action_type,
            rule_id=row.rule_id,
            effect=row.effect,
            policy_enabled=row.policy_enabled,
            payload=dict(row.payload or {}),
        )

    def to_row(self) -> AuditEventRow:
        """Build a storable row. The only API->ORM mapping."""
        return AuditEventRow(
            event_id=self.event_id,
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            seq=self.seq,
            event_type=str(self.event_type),
            actor=str(self.actor),
            created_at=self.created_at,
            summary=self.summary,
            action_type=self.action_type,
            rule_id=self.rule_id,
            effect=self.effect,
            policy_enabled=self.policy_enabled,
            payload=dict(self.payload),
        )
