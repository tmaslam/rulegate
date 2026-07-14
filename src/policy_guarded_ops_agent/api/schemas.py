"""Request/response models. This module *is* the API contract.

Every field carries a description, because these become the OpenAPI schema at
``/docs`` and that schema is what a UI developer reads instead of this code.

Design note: responses are flat and pre-rendered where a client would otherwise
have to compute something. ``deciding_rule`` and ``rationale`` are returned
directly rather than making a UI walk the ``evaluated`` list to work out which
rule mattered — two clients doing that walk would get it subtly different, and
the answer is not the client's to decide.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from policy_guarded_ops_agent.agent.state import RunStatus
from policy_guarded_ops_agent.approvals.models import ApprovalStatus
from policy_guarded_ops_agent.audit.models import AuditEvent
from policy_guarded_ops_agent.policy.models import Effect

__all__ = [
    "AblationArm",
    "AblationResponse",
    "ApprovalDecisionRequest",
    "ApprovalResponse",
    "AuditEventResponse",
    "AuditTrailResponse",
    "DecisionSummary",
    "ExecutionSummary",
    "HealthResponse",
    "PendingApprovalsResponse",
    "PolicyRulesResponse",
    "ProposalSummary",
    "RuleFireCountResponse",
    "RuleStatsResponse",
    "RuleSummary",
    "RunRequest",
    "RunResponse",
    "ViolationSummaryItem",
]


class _Schema(BaseModel):
    """Shared config for every wire model."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(_Schema):
    """Liveness. Deliberately does not call an LLM provider.

    A health check that hits a rate-limited free tier reports the container
    unhealthy the moment the quota runs out — and burns quota to do it.
    """

    status: str = Field(description='Always "ok" when the process is serving.')
    version: str = Field(description="Service version.")
    database_backend: str = Field(description='Resolved backend: "postgres" or "sqlite".')
    database_is_fallback: bool = Field(
        description=(
            "True when no DATABASE_URL was configured and the offline SQLite "
            "default is in use. A deploy that silently fell back to SQLite is "
            "visible here rather than merely looking healthy."
        )
    )
    policy_enabled: bool = Field(
        description=(
            "Whether the policy engine is ON by default. False means the guard "
            "is bypassed — an ablation configuration that must never be live."
        )
    )
    llm_providers: list[str] = Field(
        description=(
            "Resolved provider chain. Empty means no API key is configured and "
            "the deterministic fake is in use — the supported zero-account path."
        )
    )
    tracing_enabled: bool = Field(description="Whether Langfuse tracing is active.")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class RuleSummary(_Schema):
    """One business rule, as published to clients."""

    rule_id: str = Field(description="Stable id, e.g. 'refund-window-30d'.")
    description: str = Field(description="One-line statement of the rule.")


class PolicyRulesResponse(_Schema):
    """The rules this service enforces, live from the engine."""

    rules: list[RuleSummary] = Field(description="Every rule, in evaluation order.")
    escalation_threshold_usd: str = Field(
        description="Actions worth strictly more than this need a human. Decimal string."
    )
    refund_window_days: int = Field(description="Refund window in days.")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunRequest(_Schema):
    """Ask the agent to handle one customer message."""

    message: str = Field(min_length=1, max_length=8_000, description="What the customer said.")
    conversation_id: str | None = Field(
        default=None,
        description=(
            "Groups runs in the audit trail. Generated when omitted. Each run is "
            "independent — this service does not replay prior turns into the "
            "prompt; see the README's limitations."
        ),
    )
    customer_id: str | None = Field(
        default=None, description="Customer this conversation is about, when known."
    )
    policy_enabled: bool | None = Field(
        default=None,
        description=(
            "THE ABLATION FLAG. Omit to use the service default (ON). Set false "
            "to bypass the policy engine for this run and measure the violation "
            "rate without the guard. Never use false in production."
        ),
    )


class ProposalSummary(_Schema):
    """What the model proposed. Explanation only — it decided nothing."""

    action_type: str = Field(description="e.g. 'issue_refund'.")
    reasoning: str = Field(description="The model's stated reasoning. Never affects the outcome.")
    action: dict[str, Any] = Field(description="The full validated action object.")


class DecisionSummary(_Schema):
    """What the policy engine decided."""

    effect: Effect = Field(description="allow | escalate | deny.")
    deciding_rule: str | None = Field(
        description="Id of the rule that produced this effect. Null when nothing fired."
    )
    rationale: str = Field(
        description="Why, quoting the rule and the actual numbers. Safe to show a customer."
    )
    policy_enabled: bool = Field(
        description=(
            "False means the engine was BYPASSED and no rule was evaluated. Such "
            "a decision is not evidence the action was checked."
        )
    )
    evaluated: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Every rule that applied, including the ones that allowed.",
    )


class ExecutionSummary(_Schema):
    """What the tool actually did."""

    tool: str = Field(description="Tool name.")
    outcome: str = Field(description="success | not_found | rejected | error.")
    summary: str = Field(description="Human-readable result.")
    data: dict[str, Any] = Field(default_factory=dict, description="Structured result.")
    error: str | None = Field(default=None, description="Error text, when it failed.")


class ViolationSummaryItem(_Schema):
    """A rule broken by an executed action."""

    rule_id: str = Field(description="The rule that was broken.")
    severity: str = Field(description="critical (denied action ran) | high (unapproved escalation).")
    rationale: str = Field(description="What was broken, with the numbers.")


class RunResponse(_Schema):
    """The result of one run. The primary object a UI renders."""

    run_id: str = Field(description="This run. Also the LangGraph thread id.")
    conversation_id: str = Field(description="Groups runs.")
    status: RunStatus = Field(
        description=(
            "running | completed | rejected | awaiting_approval | blocked | failed. "
            "AWAITING_APPROVAL is the one to handle specially: the run is durable "
            "and paused, not finished and not errored."
        )
    )
    reply: str | None = Field(default=None, description="Customer-facing reply.")
    policy_enabled: bool = Field(description="Whether the guard was ON for this run.")
    proposal: ProposalSummary | None = Field(default=None, description="What the model proposed.")
    decision: DecisionSummary | None = Field(default=None, description="What policy decided.")
    execution: ExecutionSummary | None = Field(default=None, description="What the tool did.")
    violations: list[ViolationSummaryItem] = Field(
        default_factory=list,
        description=(
            "Rules broken by the executed action. Always computed, guard on or "
            "off. Non-empty with the guard ON would be a bug in the engine."
        ),
    )
    approval_id: str | None = Field(
        default=None,
        description=(
            "Set when status is awaiting_approval. Use it with "
            "POST /approvals/{approval_id}/decision to resume the run."
        ),
    )
    error: str | None = Field(default=None, description="Error text when status is failed.")


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalResponse(_Schema):
    """A queued approval request."""

    approval_id: str = Field(description="Id to resolve this request with.")
    thread_id: str = Field(description="The paused run's LangGraph thread. Equals its run_id.")
    conversation_id: str
    run_id: str
    status: ApprovalStatus = Field(description="pending | approved | rejected.")
    action_type: str = Field(description="What is being approved, e.g. 'issue_refund'.")
    action: dict[str, Any] = Field(description="The full proposed action.")
    rule_id: str | None = Field(description="The rule that forced this to a human.")
    rationale: str = Field(description="Why it needs approval, with the numbers.")
    value_usd: str | None = Field(description="Value at stake. Decimal string, not a float.")
    customer_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = Field(
        default=None, description="Who decided. Null while pending."
    )
    resolution_note: str | None = None


class PendingApprovalsResponse(_Schema):
    """The reviewer's work queue."""

    approvals: list[ApprovalResponse] = Field(description="Pending requests, oldest first.")
    count: int = Field(description="How many are pending.")


class ApprovalDecisionRequest(_Schema):
    """A human's verdict. Resolving this resumes the paused run synchronously."""

    approved: bool = Field(description="True to approve and execute; false to reject.")
    resolved_by: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Who is deciding. Required — an approval with no name attached is not "
            "an approval, it is an unattributed state change. This service takes "
            "the value on trust; a real deployment authenticates it (see SECURITY.md)."
        ),
    )
    note: str = Field(default="", max_length=1_024, description="Optional reviewer note.")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEventResponse(_Schema):
    """One event in the trail."""

    event_id: str
    conversation_id: str
    run_id: str
    seq: int = Field(description="Position within the run. Order by this, not by created_at.")
    event_type: str = Field(
        description=(
            "run_started | guardrail_blocked | proposal | policy_decision | "
            "approval_requested | approval_resolved | tool_call | violation | "
            "run_completed | error"
        )
    )
    actor: str = Field(description="system | llm | policy | human | tool.")
    created_at: datetime
    summary: str
    action_type: str | None = None
    rule_id: str | None = None
    effect: str | None = None
    policy_enabled: bool
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_event(cls, event: AuditEvent) -> AuditEventResponse:
        """Project the internal event onto the wire."""
        return cls(
            event_id=event.event_id,
            conversation_id=event.conversation_id,
            run_id=event.run_id,
            seq=event.seq,
            event_type=str(event.event_type),
            actor=str(event.actor),
            created_at=event.created_at,
            summary=event.summary,
            action_type=event.action_type,
            rule_id=event.rule_id,
            effect=event.effect,
            policy_enabled=event.policy_enabled,
            payload=event.payload,
        )


class AuditTrailResponse(_Schema):
    """A queried slice of the audit trail."""

    events: list[AuditEventResponse] = Field(description="Events, in trail order.")
    count: int


class RuleFireCountResponse(_Schema):
    """How often one rule fired."""

    rule_id: str
    count: int


class RuleStatsResponse(_Schema):
    """Rule fire counts, most frequent first."""

    rules: list[RuleFireCountResponse]


# ---------------------------------------------------------------------------
# Ablation
# ---------------------------------------------------------------------------


class AblationArm(_Schema):
    """One arm of the policy ON/OFF ablation.

    ``violation_rate`` is ``null`` — never ``0.0`` — when ``runs`` is 0. Zero
    violations over zero runs is the absence of a measurement, not a perfect
    score, and rendering it as 0.0 would be a fabricated number.
    """

    policy_enabled: bool = Field(description="Which arm this is.")
    runs: int = Field(description="Runs recorded in this arm.")
    violations: int = Field(description="Violations recorded in this arm.")
    violation_rate: float | None = Field(
        description="violations/runs, or null when runs is 0. Never defaulted to 0.0."
    )


class AblationResponse(_Schema):
    """The headline artifact: violation rate with the guard on vs off.

    Both arms are computed from the audit trail, so the numbers are re-derivable
    by anyone with the database rather than being asserted by this service.

    ``delta`` is null unless BOTH arms have recorded at least one run — a
    difference against an arm nobody ran is not a measurement.
    """

    policy_on: AblationArm
    policy_off: AblationArm
    delta: float | None = Field(
        description=(
            "policy_off.violation_rate - policy_on.violation_rate. Null unless "
            "both arms have data."
        )
    )
    note: str = Field(
        description=(
            "Human-readable caveat about what these numbers do and do not mean. "
            "Always read it before quoting them."
        )
    )
