"""Append-only audit store and the queries the product is judged on.

Append-only is enforced by omission: there is no ``update`` and no ``delete`` on
this class. Nothing in the service can rewrite history through this API.

Sequencing
----------
Events within a run are ordered by an explicit integer ``seq``, not by
``created_at``. Several events routinely land inside the same clock tick, and a
trail that reorders "policy denied" and "tool executed" under a reader's eyes is
worse than useless. The seq counter is per-run and allocated under a lock.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Final

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from policy_guarded_ops_agent.audit.models import (
    Actor,
    AuditEvent,
    AuditEventRow,
    AuditEventType,
)

if TYPE_CHECKING:
    from datetime import datetime

    from policy_guarded_ops_agent.storage.session import Database

__all__ = ["AuditStore", "RuleFireCount", "ViolationSummary"]

log: Final = structlog.get_logger(__name__)


class RuleFireCount(BaseModel):
    """How often one rule fired. Powers "which rule blocks the most traffic?"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    count: int


class ViolationSummary(BaseModel):
    """Violation counts for one arm of the policy ablation.

    Reported as raw counts — ``runs`` and ``violations`` — rather than a
    pre-computed rate, so the caller can attach a confidence interval. A bare
    rate with no denominator is exactly the kind of number this repo refuses to
    print.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_enabled: bool
    runs: int
    violations: int

    @property
    def violation_rate(self) -> float | None:
        """Violations per run, or ``None`` when no run has been recorded.

        ``None``, never ``0.0``: zero violations over zero runs is not a rate of
        zero, it is the absence of a measurement. Defaulting it to 0.0 would
        print a perfect score for an experiment nobody ran.
        """
        if self.runs == 0:
            return None
        return self.violations / self.runs


class AuditStore:
    """Writes and queries the audit trail."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._seq: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def _next_seq(self, run_id: str) -> int:
        """Allocate the next position in ``run_id``'s trail.

        Restart-safe. The in-memory counter is only a cache: on a cold miss the
        high-water mark is read back from the database. This matters precisely
        because of the feature this service is built around — a run pauses on
        ``interrupt()``, the process is redeployed, and a human approves an hour
        later. A purely in-memory counter would restart at 1 for that run and
        write a second event with seq=1, silently corrupting the ordering of the
        very trail the approval needs to be attributable in.
        """
        async with self._lock:
            if run_id not in self._seq:
                async with self._db.session() as session:
                    high_water = await session.scalar(
                        select(func.max(AuditEventRow.seq)).where(AuditEventRow.run_id == run_id)
                    )
                self._seq[run_id] = int(high_water or 0)
            nxt = self._seq[run_id] + 1
            self._seq[run_id] = nxt
            return nxt

    async def record(
        self,
        *,
        conversation_id: str,
        run_id: str,
        event_type: AuditEventType,
        actor: Actor,
        created_at: datetime,
        summary: str = "",
        action_type: str | None = None,
        rule_id: str | None = None,
        effect: str | None = None,
        policy_enabled: bool = True,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append one event. Returns the stored event."""
        event = AuditEvent(
            event_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            run_id=run_id,
            seq=await self._next_seq(run_id),
            event_type=event_type,
            actor=actor,
            created_at=created_at,
            summary=summary,
            action_type=action_type,
            rule_id=rule_id,
            effect=effect,
            policy_enabled=policy_enabled,
            payload=payload or {},
        )
        async with self._db.session() as session:
            session.add(event.to_row())
        log.debug("audit_recorded", event_type=str(event_type), run_id=run_id, seq=event.seq)
        return event

    async def record_many(self, events: list[AuditEvent]) -> None:
        """Append several pre-built events in one transaction.

        Used by the graph, which accumulates events in state and flushes them
        together so a partial trail cannot be committed.
        """
        if not events:
            return
        async with self._db.session() as session:
            session.add_all([e.to_row() for e in events])

    async def by_conversation(self, conversation_id: str) -> tuple[AuditEvent, ...]:
        """Every event for a conversation, in trail order."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditEventRow)
                .where(AuditEventRow.conversation_id == conversation_id)
                .order_by(AuditEventRow.run_id, AuditEventRow.seq)
            )
            return tuple(AuditEvent.from_row(row) for row in result.scalars())

    async def by_run(self, run_id: str) -> tuple[AuditEvent, ...]:
        """Every event for one run, in trail order."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditEventRow)
                .where(AuditEventRow.run_id == run_id)
                .order_by(AuditEventRow.seq)
            )
            return tuple(AuditEvent.from_row(row) for row in result.scalars())

    async def by_rule(self, rule_id: str, *, limit: int = 100) -> tuple[AuditEvent, ...]:
        """Recent events attributed to one rule. "Show me every refusal by X"."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditEventRow)
                .where(AuditEventRow.rule_id == rule_id)
                .order_by(AuditEventRow.created_at.desc())
                .limit(limit)
            )
            return tuple(AuditEvent.from_row(row) for row in result.scalars())

    async def rule_fire_counts(self) -> tuple[RuleFireCount, ...]:
        """How often each rule fired, most frequent first."""
        async with self._db.session() as session:
            result = await session.execute(
                select(AuditEventRow.rule_id, func.count().label("n"))
                .where(AuditEventRow.rule_id.is_not(None))
                .where(AuditEventRow.event_type == str(AuditEventType.POLICY_DECISION))
                .group_by(AuditEventRow.rule_id)
                .order_by(func.count().desc())
            )
            return tuple(
                RuleFireCount(rule_id=str(rule_id), count=int(count))
                for rule_id, count in result.all()
                if rule_id is not None
            )

    async def violation_summary(self, *, policy_enabled: bool) -> ViolationSummary:
        """Runs and violations for one arm of the ablation.

        The two numbers behind the headline table. Counted from the trail rather
        than from a counter held in memory, so the figure survives a restart and
        can be re-derived by anyone with the database — which is the difference
        between a measurement and an assertion.
        """
        async with self._db.session() as session:
            runs_result = await session.execute(
                select(func.count(func.distinct(AuditEventRow.run_id)))
                .where(AuditEventRow.event_type == str(AuditEventType.RUN_STARTED))
                .where(AuditEventRow.policy_enabled == policy_enabled)
            )
            violations_result = await session.execute(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == str(AuditEventType.VIOLATION))
                .where(AuditEventRow.policy_enabled == policy_enabled)
            )
            return ViolationSummary(
                policy_enabled=policy_enabled,
                runs=int(runs_result.scalar_one()),
                violations=int(violations_result.scalar_one()),
            )
