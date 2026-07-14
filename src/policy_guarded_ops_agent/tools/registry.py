"""The tool registry: executes an approved action and returns a validated result.

Scope
-----
This module *does not decide anything*. It receives an action that policy has
already cleared and performs it. Keeping the decision out of here is what lets
``policy/`` be audited as a self-contained artifact.

Idempotency
-----------
:func:`idempotency_key_for` derives a key from ``(run_id, canonical action)``
using BLAKE2b. Two properties follow, and both matter:

* **Retrying the same run's same action reuses the key**, so the billing API
  dedupes it and no second refund is issued. A timed-out call followed by a
  retry is a *normal* event in a distributed system, and without this it is a
  duplicate refund.
* **A genuinely different action gets a different key**, so a customer legitimately
  owed two refunds gets two.

The canonical form is ``model_dump_json`` over a frozen model with sorted keys,
so it is stable across processes and Python versions. Python's ``hash()`` is
salted per-process and would produce a different key on every restart — exactly
when you most need the old one.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

import structlog
from pydantic import BaseModel, ConfigDict, Field

from policy_guarded_ops_agent.billing.api import (
    BillingError,
    NotFoundError,
)
from policy_guarded_ops_agent.domain.actions import (
    CancelSubscriptionAction,
    ChangePlanAction,
    EscalateAction,
    GetCustomerAction,
    GetSubscriptionAction,
    IssueRefundAction,
    ProposedAction,
    ReplyAction,
)
from policy_guarded_ops_agent.obs.tracing import record_tool_call

if TYPE_CHECKING:
    from policy_guarded_ops_agent.billing.api import Clock, MockBillingAPI

__all__ = [
    "ExecutionRecord",
    "ToolOutcome",
    "ToolRegistry",
    "idempotency_key_for",
]

log: Final = structlog.get_logger(__name__)

_KEY_DIGEST_BYTES: Final = 16


def idempotency_key_for(run_id: str, action: ProposedAction) -> str:
    """Derive a stable dedupe key for ``action`` within ``run_id``.

    BLAKE2b over the canonical JSON of the action, salted by the run id. Stable
    across processes, restarts and machines — see the module docstring for why
    ``hash()`` is unusable here.
    """
    canonical = action.model_dump_json()
    digest = hashlib.blake2b(
        f"{run_id}:{canonical}".encode(),
        digest_size=_KEY_DIGEST_BYTES,
    )
    return digest.hexdigest()


class ToolOutcome(StrEnum):
    """How a tool call ended."""

    SUCCESS = "success"
    #: A referenced entity did not exist. Distinct from an error: the request
    #: was well-formed, the data simply is not there.
    NOT_FOUND = "not_found"
    #: The billing API refused on an integrity invariant.
    REJECTED = "rejected"
    #: Something unexpected broke.
    ERROR = "error"


class ExecutionRecord(BaseModel):
    """The result of executing one action.

    Plain-JSON ``data`` rather than a nested entity union: this lands in
    LangGraph state and must survive checkpoint serialisation and a process
    restart. Entities are dumped in JSON mode at the boundary so a ``Decimal``
    or ``datetime`` round-trips as a string rather than exploding the serialiser.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    outcome: ToolOutcome
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    executed_at: datetime
    idempotency_key: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the tool did what it was asked."""
        return self.outcome is ToolOutcome.SUCCESS


class ToolRegistry:
    """Executes approved actions against the billing API.

    Every method returns an :class:`ExecutionRecord` rather than raising for
    expected failures. A missing charge is *information the agent must tell the
    customer about*, not an exception that aborts the graph — and an aborted
    graph loses the audit trail for the turn.
    """

    def __init__(self, billing: MockBillingAPI, *, clock: Clock | None = None) -> None:
        self._billing = billing
        # Default to the billing API's own clock so records and ledger entries
        # are stamped from one source of truth.
        self._clock: Clock = clock if clock is not None else billing.clock

    def _now(self) -> datetime:
        return self._clock()

    async def execute(
        self,
        action: ProposedAction,
        *,
        run_id: str,
        approved_by: str | None = None,
    ) -> ExecutionRecord:
        """Execute ``action``, dispatching on its type.

        Args:
            action: The action to perform. Assumed already policy-cleared.
            run_id: Used to derive the idempotency key.
            approved_by: The human who approved, when this action was escalated.
                Recorded on the refund so the ledger itself carries the
                attribution, not just the audit trail.

        Returns:
            A record of what happened. Never raises for expected failures.
        """
        try:
            record = await self._dispatch(action, run_id=run_id, approved_by=approved_by)
        except NotFoundError as exc:
            record = self._failure(action, ToolOutcome.NOT_FOUND, str(exc))
        except BillingError as exc:
            record = self._failure(action, ToolOutcome.REJECTED, str(exc))
        except Exception as exc:
            # Deliberately broad: a tool failure must become a record the agent
            # can tell the customer about, never an exception that aborts the
            # graph and loses the turn's audit trail. See the class docstring.
            log.exception("tool_unexpected_error", action=str(action.action))
            record = self._failure(action, ToolOutcome.ERROR, f"{type(exc).__name__}: {exc}")

        record_tool_call(
            record.tool,
            arguments=action.model_dump_json(),
            result=record.summary,
            error=record.error,
        )
        return record

    async def _dispatch(  # noqa: PLR0911
        self,
        action: ProposedAction,
        *,
        run_id: str,
        approved_by: str | None,
    ) -> ExecutionRecord:
        """Route to the right tool. Exhaustive over the action union.

        PLR0911 (too many returns) is waived: one return per union member is
        exactly what an exhaustive dispatch looks like, and mypy checks the match
        for exhaustiveness. Collapsing it into a lookup table to satisfy the
        counter would trade a checked dispatch for an unchecked one.
        """
        match action:
            case GetCustomerAction():
                return await self.get_customer(action)
            case GetSubscriptionAction():
                return await self.get_subscription(action)
            case IssueRefundAction():
                return await self.issue_refund(action, run_id=run_id, approved_by=approved_by)
            case ChangePlanAction():
                return await self.change_plan(action)
            case CancelSubscriptionAction():
                return await self.cancel(action)
            case EscalateAction():
                return self.escalate(action)
            case ReplyAction():
                return self.reply(action)

    def _failure(
        self,
        action: ProposedAction,
        outcome: ToolOutcome,
        message: str,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            tool=str(action.action),
            outcome=outcome,
            summary=message,
            error=message,
            executed_at=self._now(),
        )

    # -- reads ---------------------------------------------------------------

    async def get_customer(self, action: GetCustomerAction) -> ExecutionRecord:
        """Fetch a customer with their subscriptions and recent charges."""
        customer = await self._billing.get_customer(action.customer_id)
        subscriptions = await self._billing.list_subscriptions(action.customer_id)
        charges = await self._billing.list_charges(action.customer_id)
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=(
                f"{customer.name} ({customer.email}) — {len(subscriptions)} subscription(s), "
                f"{len(charges)} charge(s)"
            ),
            data={
                "customer": customer.model_dump(mode="json"),
                "subscriptions": [s.model_dump(mode="json") for s in subscriptions],
                "charges": [c.model_dump(mode="json") for c in charges],
            },
            executed_at=self._now(),
        )

    async def get_subscription(self, action: GetSubscriptionAction) -> ExecutionRecord:
        """Fetch a subscription."""
        subscription = await self._billing.get_subscription(action.subscription_id)
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=(
                f"{subscription.id}: {subscription.plan} / {subscription.status}, "
                f"period ends {subscription.current_period_end.date().isoformat()}"
            ),
            data={"subscription": subscription.model_dump(mode="json")},
            executed_at=self._now(),
        )

    # -- writes --------------------------------------------------------------

    async def issue_refund(
        self,
        action: IssueRefundAction,
        *,
        run_id: str,
        approved_by: str | None = None,
    ) -> ExecutionRecord:
        """Issue a refund. Idempotent within a run."""
        key = idempotency_key_for(run_id, action)
        refund = await self._billing.issue_refund(
            charge_id=action.charge_id,
            amount_usd=action.amount_usd,
            reason=action.reason,
            idempotency_key=key,
            approved_by=approved_by,
        )
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=f"refunded ${refund.amount_usd} against {refund.charge_id} ({refund.id})",
            data={"refund": refund.model_dump(mode="json")},
            executed_at=self._now(),
            idempotency_key=key,
        )

    async def change_plan(self, action: ChangePlanAction) -> ExecutionRecord:
        """Change a subscription's plan."""
        subscription = await self._billing.change_plan(
            subscription_id=action.subscription_id,
            target_plan=action.target_plan,
            prorate=action.prorate,
        )
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=(
                f"{subscription.id} moved to {subscription.plan}"
                f"{' with proration' if action.prorate else ''}"
            ),
            data={"subscription": subscription.model_dump(mode="json")},
            executed_at=self._now(),
        )

    async def cancel(self, action: CancelSubscriptionAction) -> ExecutionRecord:
        """Cancel a subscription."""
        subscription = await self._billing.cancel_subscription(
            subscription_id=action.subscription_id,
            at_period_end=action.at_period_end,
        )
        when = "at period end" if action.at_period_end else "immediately"
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=f"{subscription.id} cancelled {when}",
            data={"subscription": subscription.model_dump(mode="json")},
            executed_at=self._now(),
        )

    # -- non-billing ---------------------------------------------------------

    def escalate(self, action: EscalateAction) -> ExecutionRecord:
        """Hand off to a human.

        The ticket id is derived from the reason so it is deterministic in tests.
        In a real deployment this would call the ticketing system; that is out of
        scope, and the record says so rather than implying a queue exists.
        """
        ticket = hashlib.blake2b(action.reason.encode(), digest_size=4).hexdigest()
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=f"escalated to a human: {action.reason}",
            data={
                "ticket_id": f"esc_{ticket}",
                "reason": action.reason,
                "summary": action.summary,
                "note": "mock handoff — no ticketing system is integrated in this demo",
            },
            executed_at=self._now(),
        )

    def reply(self, action: ReplyAction) -> ExecutionRecord:
        """Answer the customer. Touches no billing state."""
        return ExecutionRecord(
            tool=str(action.action),
            outcome=ToolOutcome.SUCCESS,
            summary=action.message,
            data={"message": action.message},
            executed_at=self._now(),
        )
