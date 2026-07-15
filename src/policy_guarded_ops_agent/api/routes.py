"""HTTP routes.

Run/resume symmetry
-------------------
``POST /runs`` starts a run. If policy escalates, the graph pauses on
``interrupt()`` and the response is ``status=awaiting_approval`` plus an
``approval_id``. ``POST /approvals/{id}/decision`` resolves the queue row and
resumes the *same* paused graph thread with ``Command(resume=...)``, returning
the completed :class:`RunResponse`.

Both endpoints return the same shape, so a UI renders one component either way.

The resolve-then-resume ordering is deliberate: the queue's conditional update is
what breaks a double-approve race (see ``approvals/queue.py``). Resolving first
means the loser of that race gets a 409 and the graph is resumed exactly once. If
we resumed first and recorded after, both racers would resume the run and the
refund would be issued twice — which is the exact failure this whole design
exists to prevent.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from langgraph.types import Command

from policy_guarded_ops_agent.agent.state import RunStatus
from policy_guarded_ops_agent.api.deps import get_runtime
from policy_guarded_ops_agent.api.schemas import (
    AblationArm,
    AblationResponse,
    ApprovalDecisionRequest,
    ApprovalResponse,
    AuditEventResponse,
    AuditTrailResponse,
    DecisionSummary,
    ExecutionSummary,
    HealthResponse,
    PendingApprovalsResponse,
    PolicyRulesResponse,
    ProposalSummary,
    RuleFireCountResponse,
    RuleStatsResponse,
    RuleSummary,
    RunRequest,
    RunResponse,
    ViolationSummaryItem,
)
from policy_guarded_ops_agent.approvals.models import ApprovalDecision, ApprovalRequest
from policy_guarded_ops_agent.approvals.queue import AlreadyResolvedError, ApprovalNotFoundError
from policy_guarded_ops_agent.obs.tracing import TracingConfig

if TYPE_CHECKING:
    # RunnableConfig appears only on local-variable annotations, which Python
    # never evaluates at runtime (PEP 526). Unlike the FastAPI signature
    # annotations below, it is safe behind TYPE_CHECKING.
    from langchain_core.runnables import RunnableConfig

    from policy_guarded_ops_agent.agent.state import AgentState
    from policy_guarded_ops_agent.runtime import AgentRuntime

__all__ = ["router"]

log: Final = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _approval_to_response(request: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=request.approval_id,
        thread_id=request.thread_id,
        conversation_id=request.conversation_id,
        run_id=request.run_id,
        status=request.status,
        action_type=request.action_type,
        action=request.action_payload,
        rule_id=request.rule_id,
        rationale=request.rationale,
        value_usd=request.value_usd,
        customer_id=request.customer_id,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        resolved_by=request.resolved_by,
        resolution_note=request.resolution_note,
    )


def _state_to_run_response(
    state: dict[str, Any],
    *,
    run_id: str,
    conversation_id: str,
    policy_enabled: bool,
    interrupted: bool,
    approval_id: str | None,
) -> RunResponse:
    """Project graph state onto the wire model.

    ``interrupted`` is passed in rather than inferred from state: when the graph
    pauses, the ``human_review`` node never returned, so nothing in state says
    "paused". That fact lives in the invoke result's ``__interrupt__`` key and
    only the caller has seen it.
    """
    proposal = state.get("proposal")
    decision = state.get("decision")
    execution = state.get("execution")

    run_status = (
        RunStatus.AWAITING_APPROVAL if interrupted else state.get("status", RunStatus.FAILED)
    )

    return RunResponse(
        run_id=run_id,
        conversation_id=conversation_id,
        status=run_status,
        reply=state.get("reply"),
        policy_enabled=policy_enabled,
        proposal=(
            ProposalSummary(
                action_type=str(proposal.action.action),
                reasoning=proposal.reasoning,
                action=proposal.action.model_dump(mode="json"),
            )
            if proposal is not None
            else None
        ),
        decision=(
            DecisionSummary(
                effect=decision.effect,
                deciding_rule=str(decision.deciding_rule) if decision.deciding_rule else None,
                rationale=decision.rationale,
                policy_enabled=decision.policy_enabled,
                evaluated=[
                    {
                        "rule_id": str(o.rule_id),
                        "effect": str(o.effect),
                        "rationale": o.rationale,
                        "evidence": dict(o.evidence),
                    }
                    for o in decision.evaluated
                ],
            )
            if decision is not None
            else None
        ),
        execution=(
            ExecutionSummary(
                tool=execution.tool,
                outcome=str(execution.outcome),
                summary=execution.summary,
                data=execution.data,
                error=execution.error,
            )
            if execution is not None
            else None
        ),
        violations=[
            ViolationSummaryItem(
                rule_id=str(v.rule_id),
                severity=str(v.severity),
                rationale=v.rationale,
            )
            for v in state.get("violations", [])
        ],
        approval_id=approval_id,
        error=state.get("error"),
    )


def _extract_interrupt_approval_id(result: dict[str, Any]) -> str | None:
    """Pull the approval id out of a paused invoke result.

    LangGraph reports a pause via the ``__interrupt__`` key, carrying the value
    passed to ``interrupt()``. Read defensively: this is framework-shaped data,
    and a shape change should degrade to "paused, id unknown" rather than a 500
    on a run that genuinely did pause correctly.
    """
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None)
    if isinstance(value, dict):
        approval_id = value.get("approval_id")
        if isinstance(approval_id, str):
            return approval_id
    return None


# ---------------------------------------------------------------------------
# Health & policy
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["ops"],
    summary="Liveness probe",
)
async def health(rt: AgentRuntime = Depends(get_runtime)) -> HealthResponse:
    """Liveness. Never calls an LLM provider — see HealthResponse."""
    from policy_guarded_ops_agent import __version__  # noqa: PLC0415 — avoids an import cycle.

    return HealthResponse(
        status="ok",
        version=__version__,
        database_backend=str(rt.database.config.backend),
        database_is_fallback=rt.database.config.is_fallback,
        policy_enabled=rt.settings.policy_enabled,
        llm_providers=[s.name for s in rt.deps.gateway.chain],
        tracing_enabled=TracingConfig().enabled,
    )


@router.get(
    "/policy/rules",
    response_model=PolicyRulesResponse,
    tags=["policy"],
    summary="The business rules this service enforces",
)
async def policy_rules(rt: AgentRuntime = Depends(get_runtime)) -> PolicyRulesResponse:
    """The live rule set, read from the engine rather than a hand-kept list."""
    return PolicyRulesResponse(
        rules=[
            RuleSummary(rule_id=str(d.rule_id), description=d.description)
            for d in rt.deps.engine.describe()
        ],
        escalation_threshold_usd=str(rt.settings.escalation_threshold_usd),
        refund_window_days=rt.settings.refund_window_days,
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post(
    "/runs",
    response_model=RunResponse,
    tags=["agent"],
    summary="Run the agent on one customer message",
    status_code=status.HTTP_200_OK,
)
async def create_run(
    body: RunRequest,
    rt: AgentRuntime = Depends(get_runtime),
) -> RunResponse:
    """Handle one message end to end.

    Returns ``status=awaiting_approval`` with an ``approval_id`` when policy
    escalated: the run is durable and paused, not failed. Resume it via
    ``POST /approvals/{approval_id}/decision``.
    """
    run_id = uuid.uuid4().hex
    conversation_id = body.conversation_id or uuid.uuid4().hex
    policy_enabled = (
        body.policy_enabled if body.policy_enabled is not None else rt.settings.policy_enabled
    )
    if not policy_enabled:
        log.warning(
            "run_with_policy_disabled",
            run_id=run_id,
            note="ablation arm — the policy engine will be bypassed",
        )

    initial: AgentState = {
        "conversation_id": conversation_id,
        "run_id": run_id,
        "user_message": body.message,
        "customer_id": body.customer_id,
        "policy_enabled": policy_enabled,
        "status": RunStatus.RUNNING,
        "audit": [],
        "violations": [],
    }
    # thread_id == run_id: one graph thread per run, so an approval targets
    # exactly one paused run and cannot resume a different turn.
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    result = await rt.graph.ainvoke(initial, config=config)

    approval_id = _extract_interrupt_approval_id(result)
    return _state_to_run_response(
        result,
        run_id=run_id,
        conversation_id=conversation_id,
        policy_enabled=policy_enabled,
        interrupted=approval_id is not None,
        approval_id=approval_id,
    )


# ---------------------------------------------------------------------------
# Approvals: human-in-the-loop
# ---------------------------------------------------------------------------


@router.get(
    "/approvals",
    response_model=PendingApprovalsResponse,
    tags=["approvals"],
    summary="Pending approvals — the reviewer's queue",
)
async def list_approvals(
    limit: int = Query(default=50, ge=1, le=200),
    rt: AgentRuntime = Depends(get_runtime),
) -> PendingApprovalsResponse:
    """Every run currently paused waiting for a human, oldest first."""
    pending = await rt.deps.approvals.list_pending(limit=limit)
    return PendingApprovalsResponse(
        approvals=[_approval_to_response(a) for a in pending],
        count=len(pending),
    )


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalResponse,
    tags=["approvals"],
    summary="One approval request",
    responses={404: {"description": "No such approval request."}},
)
async def get_approval(
    approval_id: str,
    rt: AgentRuntime = Depends(get_runtime),
) -> ApprovalResponse:
    """Fetch one request, pending or resolved."""
    try:
        request = await rt.deps.approvals.get(approval_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _approval_to_response(request)


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=RunResponse,
    tags=["approvals"],
    summary="Approve or reject, and resume the paused run",
    responses={
        404: {"description": "No such approval request."},
        409: {"description": "Already resolved by someone else. The run was already resumed."},
    },
)
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    rt: AgentRuntime = Depends(get_runtime),
) -> RunResponse:
    """Resolve an approval and resume its run, returning the finished result.

    Resolve-then-resume — see the module docstring for why that order is what
    makes a double-approve safe.
    """
    try:
        request = await rt.deps.approvals.get(approval_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    decision = ApprovalDecision(
        approved=body.approved,
        resolved_by=body.resolved_by,
        note=body.note,
    )
    try:
        await rt.deps.approvals.resolve(approval_id, decision, resolved_at=rt.deps.clock())
    except AlreadyResolvedError as exc:
        # 409, not 500: the request is well-formed, it simply lost the race. The
        # winner already resumed the run — resuming again would double-execute.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    config: RunnableConfig = {"configurable": {"thread_id": request.thread_id}}
    result = await rt.graph.ainvoke(
        Command(resume=decision.model_dump(mode="json")), config=config
    )
    return _state_to_run_response(
        result,
        run_id=request.run_id,
        conversation_id=request.conversation_id,
        policy_enabled=bool(result.get("policy_enabled", True)),
        interrupted=False,
        approval_id=approval_id,
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@router.get(
    "/audit/conversations/{conversation_id}",
    response_model=AuditTrailResponse,
    tags=["audit"],
    summary="Full trail for a conversation",
)
async def audit_by_conversation(
    conversation_id: str,
    rt: AgentRuntime = Depends(get_runtime),
) -> AuditTrailResponse:
    """Every recorded event for a conversation, in trail order."""
    events = await rt.deps.audit.by_conversation(conversation_id)
    return AuditTrailResponse(
        events=[AuditEventResponse.from_event(e) for e in events],
        count=len(events),
    )


@router.get(
    "/audit/runs/{run_id}",
    response_model=AuditTrailResponse,
    tags=["audit"],
    summary="Full trail for one run",
)
async def audit_by_run(
    run_id: str,
    rt: AgentRuntime = Depends(get_runtime),
) -> AuditTrailResponse:
    """Every recorded event for one run, ordered by seq."""
    events = await rt.deps.audit.by_run(run_id)
    return AuditTrailResponse(
        events=[AuditEventResponse.from_event(e) for e in events],
        count=len(events),
    )


@router.get(
    "/audit/rules/{rule_id}",
    response_model=AuditTrailResponse,
    tags=["audit"],
    summary="Every decision attributed to one rule",
)
async def audit_by_rule(
    rule_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    rt: AgentRuntime = Depends(get_runtime),
) -> AuditTrailResponse:
    """"Show me every time refund-window-30d refused someone." """
    events = await rt.deps.audit.by_rule(rule_id, limit=limit)
    return AuditTrailResponse(
        events=[AuditEventResponse.from_event(e) for e in events],
        count=len(events),
    )


@router.get(
    "/audit/rule-stats",
    response_model=RuleStatsResponse,
    tags=["audit"],
    summary="How often each rule fired",
)
async def rule_stats(rt: AgentRuntime = Depends(get_runtime)) -> RuleStatsResponse:
    """Fire counts per rule, most frequent first."""
    counts = await rt.deps.audit.rule_fire_counts()
    return RuleStatsResponse(
        rules=[RuleFireCountResponse(rule_id=c.rule_id, count=c.count) for c in counts]
    )


# ---------------------------------------------------------------------------
# Ablation — the headline artifact
# ---------------------------------------------------------------------------

_ABLATION_NOTE: Final = (
    "Violation rate = violations / runs, counted from the audit trail for each "
    "arm of the policy_enabled flag. Both arms run the identical agent, prompt "
    "and scenarios; the only difference is whether the policy engine gates the "
    "proposed action. Violations are detected by the SAME rule objects the "
    "engine gates on, applied after execution, so the two arms are comparable "
    "by construction. A null rate means that arm has no recorded runs — it is "
    "not a rate of zero. These are counts from whatever traffic this instance "
    "has served; they are NOT a benchmark unless the runs came from a fixed "
    "scenario set, and a run against the deterministic fake provider measures "
    "the scaffold, not any model."
)


@router.get(
    "/ablation",
    response_model=AblationResponse,
    tags=["policy"],
    summary="Violation rate with the policy engine on vs off",
)
async def ablation(rt: AgentRuntime = Depends(get_runtime)) -> AblationResponse:
    """The headline artifact: what the guard is actually worth.

    Computed from the trail, so anyone with the database can re-derive it.
    """
    on = await rt.deps.audit.violation_summary(policy_enabled=True)
    off = await rt.deps.audit.violation_summary(policy_enabled=False)

    on_rate = on.violation_rate
    off_rate = off.violation_rate
    # Null unless BOTH arms have data. A delta against an arm nobody ran is not
    # a measurement, and rendering it as a number would be a fabrication.
    delta = off_rate - on_rate if (on_rate is not None and off_rate is not None) else None

    return AblationResponse(
        policy_on=AblationArm(
            policy_enabled=True, runs=on.runs, violations=on.violations, violation_rate=on_rate
        ),
        policy_off=AblationArm(
            policy_enabled=False, runs=off.runs, violations=off.violations, violation_rate=off_rate
        ),
        delta=delta,
        note=_ABLATION_NOTE,
    )
