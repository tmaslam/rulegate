"""The graph's nodes. One node talks to the model; a different node decides.

Each node returns a **partial** state update. Nothing mutates state in place —
LangGraph merges the returned dict, and the ``audit`` key is concatenated by the
reducer declared in ``state.py``.

The interrupt gotcha (read before editing ``human_review``)
-----------------------------------------------------------
``interrupt()`` does not suspend a coroutine. It raises, and on resume
**LangGraph re-executes the node from its first line**. Every side effect before
the ``interrupt()`` call therefore happens *twice*: once on the pause, once on
the resume.

Here that would mean enqueuing the same approval twice — one row for the human
to action and one orphan, plus a duplicated audit event. ``human_review`` guards
against it by looking the request up by ``thread_id`` first and only enqueuing
when it is genuinely absent. The unique index on ``thread_id`` is the backstop
if that check is ever removed.

State updates returned *before* the interrupt are discarded (the node never
returned), so only side effects need this treatment — not the audit events in
the return value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import structlog
from langgraph.types import interrupt
from pydantic import ValidationError

from policy_guarded_ops_agent.agent.context import resolve_policy_context
from policy_guarded_ops_agent.agent.prompts import SYSTEM_PROMPT, build_user_prompt
from policy_guarded_ops_agent.agent.state import AgentState, RunStatus
from policy_guarded_ops_agent.approvals.models import ApprovalDecision
from policy_guarded_ops_agent.audit.models import Actor, AuditEventType
from policy_guarded_ops_agent.domain.actions import AgentProposal
from policy_guarded_ops_agent.guardrails.base import InputContext, OutputContext
from policy_guarded_ops_agent.llm.gateway import (
    ChatMessage,
    CompletionRequest,
    GatewayError,
    StructuredOutputError,
)
from policy_guarded_ops_agent.obs.tracing import llm_span
from policy_guarded_ops_agent.policy.models import Effect, Violation
from policy_guarded_ops_agent.policy.rules import action_value_usd

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from policy_guarded_ops_agent.agent.deps import AgentDeps

__all__ = ["AgentNodes"]

log: Final = structlog.get_logger(__name__)


class AgentNodes:
    """Node implementations, bound to their dependencies.

    A class rather than closures so each node is individually addressable in
    tests: ``AgentNodes(deps).decide(state)`` is a plain coroutine you can call
    without standing up a graph.
    """

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps

    # -- 1. guardrails -------------------------------------------------------

    async def guard_input(self, state: AgentState) -> dict[str, Any]:
        """Run input guardrails. Blocks before any model call — and any cost."""
        deps = self._deps
        now = deps.clock()
        run_id = state["run_id"]
        conversation_id = state["conversation_id"]
        policy_enabled = state["policy_enabled"]

        started = await deps.audit.record(
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=AuditEventType.RUN_STARTED,
            actor=Actor.SYSTEM,
            created_at=now,
            summary=f"run started (policy_enabled={policy_enabled})",
            policy_enabled=policy_enabled,
            payload={"user_message": state["user_message"]},
        )

        decision = deps.guardrails.check_input(InputContext(text=state["user_message"]))
        if not decision.allowed and decision.refusal is not None:
            blocked = await deps.audit.record(
                conversation_id=conversation_id,
                run_id=run_id,
                event_type=AuditEventType.GUARDRAIL_BLOCKED,
                actor=Actor.SYSTEM,
                created_at=now,
                summary=decision.refusal.reason,
                policy_enabled=policy_enabled,
                payload={
                    "code": str(decision.refusal.code),
                    "filter": decision.refusal.filter_name,
                },
            )
            return {
                "status": RunStatus.BLOCKED,
                "reply": decision.refusal.user_message,
                "audit": [started, blocked],
            }

        # `content` carries any redactions applied by the pipeline; using the raw
        # message here would send the un-redacted card number to the provider.
        return {
            "status": RunStatus.RUNNING,
            "user_message": decision.content or state["user_message"],
            "audit": [started],
        }

    # -- 2. propose (the ONLY node that talks to a model) --------------------

    async def propose(self, state: AgentState) -> dict[str, Any]:
        """Ask the model for one validated action.

        Uses ``acomplete_model``: the JSON schema goes to the provider and the
        body is parsed by Pydantic. A malformed body raises and is reported —
        it is never salvaged by regex, and never partially trusted.
        """
        deps = self._deps
        now = deps.clock()
        request = CompletionRequest(
            messages=(
                ChatMessage(role="system", content=SYSTEM_PROMPT, cacheable=True),
                ChatMessage(
                    role="user",
                    content=build_user_prompt(
                        state["user_message"],
                        customer_id=state.get("customer_id"),
                    ),
                ),
            ),
            temperature=deps.settings.llm_temperature,
            seed=deps.settings.llm_seed,
            metadata={"run_id": state["run_id"], "node": "propose"},
        )

        try:
            with llm_span(request, session_id=state["conversation_id"]) as span:
                proposal, response = await deps.gateway.acomplete_model(request, AgentProposal)
                span.record_response(response)
        except (StructuredOutputError, GatewayError, ValidationError) as exc:
            # A model that cannot produce a valid action is a failed turn, not a
            # licence to improvise one. Fail loudly and record why.
            log.warning("proposal_failed", error=str(exc), run_id=state["run_id"])
            event = await deps.audit.record(
                conversation_id=state["conversation_id"],
                run_id=state["run_id"],
                event_type=AuditEventType.ERROR,
                actor=Actor.LLM,
                created_at=now,
                summary=f"proposal failed: {type(exc).__name__}",
                policy_enabled=state["policy_enabled"],
                payload={"error": str(exc)},
            )
            return {
                "status": RunStatus.FAILED,
                "error": f"{type(exc).__name__}: {exc}",
                "reply": (
                    "I couldn't process that request just now. Please try again, "
                    "or ask for a human."
                ),
                "audit": [event],
            }

        event = await deps.audit.record(
            conversation_id=state["conversation_id"],
            run_id=state["run_id"],
            event_type=AuditEventType.PROPOSAL,
            actor=Actor.LLM,
            created_at=now,
            summary=f"proposed {proposal.action.action}: {proposal.reasoning}",
            action_type=str(proposal.action.action),
            policy_enabled=state["policy_enabled"],
            payload={
                "action": proposal.action.model_dump(mode="json"),
                "reasoning": proposal.reasoning,
                "provider": response.provider,
                "model": response.model,
            },
        )
        return {"proposal": proposal, "audit": [event]}

    # -- 3. decide (the ONLY node that determines what happens) --------------

    async def decide(self, state: AgentState) -> dict[str, Any]:
        """Gate the proposed action through the policy engine.

        The ablation lives here, in one branch, at one call site. With the guard
        off the engine is *bypassed* — not softened — and the resulting decision
        records ``policy_enabled=False`` with zero rules evaluated, so no reader
        of the trail can mistake it for a clean check.
        """
        deps = self._deps
        proposal = state.get("proposal")
        if proposal is None:  # pragma: no cover — routing guarantees a proposal.
            return {"status": RunStatus.FAILED, "error": "decide reached with no proposal"}

        now = deps.clock()
        action = proposal.action
        ctx = await resolve_policy_context(
            deps.billing, action, now=now, customer_id=state.get("customer_id")
        )

        decision = (
            deps.engine.evaluate(action, ctx)
            if state["policy_enabled"]
            else deps.engine.bypass(action, ctx)
        )

        event = await deps.audit.record(
            conversation_id=state["conversation_id"],
            run_id=state["run_id"],
            event_type=AuditEventType.POLICY_DECISION,
            actor=Actor.POLICY,
            created_at=now,
            summary=decision.rationale,
            action_type=str(action.action),
            rule_id=str(decision.deciding_rule) if decision.deciding_rule else None,
            effect=str(decision.effect),
            policy_enabled=state["policy_enabled"],
            payload={
                "effect": str(decision.effect),
                "evaluated": [
                    {
                        "rule_id": str(o.rule_id),
                        "effect": str(o.effect),
                        "rationale": o.rationale,
                        "evidence": dict(o.evidence),
                    }
                    for o in decision.evaluated
                ],
            },
        )
        return {"decision": decision, "audit": [event]}

    # -- 4. human review (HITL) ---------------------------------------------

    async def human_review(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Pause for human approval, durably.

        Everything before ``interrupt()`` re-runs on resume — see the module
        docstring. Hence the lookup-before-enqueue.
        """
        deps = self._deps
        decision = state["decision"]
        if decision is None:  # pragma: no cover — routing guarantees a decision.
            return {"status": RunStatus.FAILED, "error": "human_review reached with no decision"}

        thread_id = str(config["configurable"]["thread_id"])
        action = decision.action
        now = deps.clock()

        # --- side effect, guarded against the resume replay -----------------
        existing = await deps.approvals.get_by_thread(thread_id)
        new_events = []
        if existing is None:
            ctx = await resolve_policy_context(
                deps.billing, action, now=now, customer_id=state.get("customer_id")
            )
            value = action_value_usd(action, ctx)
            request = await deps.approvals.enqueue(
                thread_id=thread_id,
                conversation_id=state["conversation_id"],
                run_id=state["run_id"],
                action_type=str(action.action),
                action_payload=action.model_dump(mode="json"),
                created_at=now,
                rule_id=str(decision.deciding_rule) if decision.deciding_rule else None,
                rationale=decision.rationale,
                value_usd=str(value) if value is not None else None,
                customer_id=state.get("customer_id"),
            )
            new_events.append(
                await deps.audit.record(
                    conversation_id=state["conversation_id"],
                    run_id=state["run_id"],
                    event_type=AuditEventType.APPROVAL_REQUESTED,
                    actor=Actor.POLICY,
                    created_at=now,
                    summary=f"awaiting human approval: {decision.rationale}",
                    action_type=str(action.action),
                    rule_id=str(decision.deciding_rule) if decision.deciding_rule else None,
                    effect=str(Effect.ESCALATE),
                    policy_enabled=state["policy_enabled"],
                    payload={"approval_id": request.approval_id, "thread_id": thread_id},
                )
            )
        else:
            request = existing

        # --- the pause ------------------------------------------------------
        # Raises GraphInterrupt on the first pass; returns the resume payload on
        # the second. Everything above this line has now run twice.
        raw: Any = interrupt(
            {
                "approval_id": request.approval_id,
                "action": action.model_dump(mode="json"),
                "rule_id": str(decision.deciding_rule) if decision.deciding_rule else None,
                "rationale": decision.rationale,
                "value_usd": request.value_usd,
            }
        )

        # --- resumed --------------------------------------------------------
        try:
            approval = ApprovalDecision.model_validate(raw)
        except ValidationError as exc:
            # The resume payload is external input and is validated like any
            # other. An unparseable one must never be read as an approval.
            log.exception("invalid_resume_payload", error=str(exc), thread_id=thread_id)
            return {
                "status": RunStatus.FAILED,
                "error": f"invalid approval payload: {exc}",
                "reply": "Something went wrong while processing the approval.",
            }

        resumed_at = deps.clock()
        resolved_event = await deps.audit.record(
            conversation_id=state["conversation_id"],
            run_id=state["run_id"],
            event_type=AuditEventType.APPROVAL_RESOLVED,
            actor=Actor.HUMAN,
            created_at=resumed_at,
            summary=(
                f"{'approved' if approval.approved else 'rejected'} by {approval.resolved_by}"
                f"{f': {approval.note}' if approval.note else ''}"
            ),
            action_type=str(action.action),
            effect=str(Effect.ALLOW if approval.approved else Effect.DENY),
            policy_enabled=state["policy_enabled"],
            payload={
                "approval_id": request.approval_id,
                "approved": approval.approved,
                "resolved_by": approval.resolved_by,
            },
        )
        return {
            "approval": approval,
            "approval_id": request.approval_id,
            "audit": [*new_events, resolved_event],
        }

    # -- 5. execute ----------------------------------------------------------

    async def execute(self, state: AgentState) -> dict[str, Any]:
        """Run the tool, then audit the executed action against the same rules.

        The violation check runs on **both** arms of the ablation. That is the
        measurement: with the guard on, an illegal action never reaches this
        node, so the count is zero by construction; with the guard off it
        reaches here and is counted. The delta is what the headline table
        reports.
        """
        deps = self._deps
        decision = state["decision"]
        if decision is None:  # pragma: no cover — routing guarantees a decision.
            return {"status": RunStatus.FAILED, "error": "execute reached with no decision"}

        action = decision.action
        approval = state.get("approval")
        approved_by = approval.resolved_by if approval is not None else None
        now = deps.clock()

        # Facts as they were BEFORE execution. Resolving after would let a
        # refund's own effect on the balance mask the violation it committed.
        pre_ctx = await resolve_policy_context(
            deps.billing, action, now=now, customer_id=state.get("customer_id")
        )

        record = await deps.tools.execute(action, run_id=state["run_id"], approved_by=approved_by)

        events = [
            await deps.audit.record(
                conversation_id=state["conversation_id"],
                run_id=state["run_id"],
                event_type=AuditEventType.TOOL_CALL,
                actor=Actor.TOOL,
                created_at=now,
                summary=record.summary,
                action_type=record.tool,
                policy_enabled=state["policy_enabled"],
                payload={
                    "outcome": str(record.outcome),
                    "data": record.data,
                    "idempotency_key": record.idempotency_key,
                    "error": record.error,
                },
            )
        ]

        violations: tuple[Violation, ...] = ()
        if record.succeeded:
            violations = deps.detector.detect(
                action, pre_ctx, human_approved=approval is not None and approval.approved
            )
            events.extend(
                [
                    await deps.audit.record(
                        conversation_id=state["conversation_id"],
                        run_id=state["run_id"],
                        event_type=AuditEventType.VIOLATION,
                        actor=Actor.SYSTEM,
                        created_at=now,
                        summary=violation.rationale,
                        action_type=violation.action_type,
                        rule_id=str(violation.rule_id),
                        effect=str(Effect.DENY),
                        policy_enabled=state["policy_enabled"],
                        payload={
                            "severity": str(violation.severity),
                            "evidence": dict(violation.evidence),
                        },
                    )
                    for violation in violations
                ]
            )

        return {
            "execution": record,
            "violations": list(violations),
            "audit": events,
        }

    # -- 6. respond ----------------------------------------------------------

    async def respond(self, state: AgentState) -> dict[str, Any]:
        """Build the customer-facing reply and run output guardrails."""
        deps = self._deps
        now = deps.clock()
        reply, status = self._compose_reply(state)

        guarded = deps.guardrails.check_output(OutputContext(text=reply))
        if not guarded.allowed and guarded.refusal is not None:
            reply = guarded.refusal.user_message

        event = await deps.audit.record(
            conversation_id=state["conversation_id"],
            run_id=state["run_id"],
            event_type=AuditEventType.RUN_COMPLETED,
            actor=Actor.SYSTEM,
            created_at=now,
            summary=f"run finished: {status}",
            policy_enabled=state["policy_enabled"],
            payload={"status": str(status), "reply": reply},
        )
        return {"reply": reply, "status": status, "audit": [event]}

    @staticmethod
    def _compose_reply(state: AgentState) -> tuple[str, RunStatus]:
        """Turn the run's outcome into something a customer can read.

        Deterministic string building, not a second LLM call: the reply for a
        refusal must quote the rule verbatim, and paying a model to paraphrase a
        rule it might get wrong would be both slower and less trustworthy.
        """
        decision = state.get("decision")
        execution = state.get("execution")
        approval = state.get("approval")

        if approval is not None and not approval.approved:
            note = f" ({approval.note})" if approval.note else ""
            return (
                f"A member of our team reviewed this and wasn't able to approve it{note}.",
                RunStatus.REJECTED,
            )

        if decision is not None and decision.effect is Effect.DENY:
            # Quote the rule. The customer is entitled to know exactly what
            # stopped their request, not a vague "we can't do that".
            return (
                f"I can't do that: {decision.rationale}",
                RunStatus.REJECTED,
            )

        if execution is not None:
            if execution.succeeded:
                return execution.summary, RunStatus.COMPLETED
            return (
                f"I wasn't able to complete that: {execution.summary}",
                RunStatus.FAILED,
            )

        return ("I wasn't able to determine what to do with that request.", RunStatus.FAILED)
