"""The graph's typed state.

Why TypedDict and not a Pydantic model
--------------------------------------
LangGraph merges node return values into the state dict key-by-key, and it
checkpoints the whole thing. A ``TypedDict`` is what that mechanism is designed
around: partial updates are just dicts with a subset of keys, which a frozen
Pydantic model cannot express without a copy-on-every-node dance.

The values inside are still Pydantic models. So the *container* is a TypedDict
the framework can merge and serialise, while every value crossing a boundary is
validated. That is the trade actually worth making.

Serialisability
---------------
**Every value here must survive a checkpoint round-trip.** The whole point of
the HITL design is that the process can die while a run is paused and resume
after a redeploy. A value that cannot be serialised will not fail at write time
— it will fail when someone approves a refund an hour later. Hence: Pydantic
models (which LangGraph's serialiser handles) and JSON-safe primitives only. No
open connections, no callables, no live billing objects.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from policy_guarded_ops_agent.approvals.models import ApprovalDecision
from policy_guarded_ops_agent.audit.models import AuditEvent
from policy_guarded_ops_agent.domain.actions import AgentProposal
from policy_guarded_ops_agent.policy.models import PolicyDecision, Violation
from policy_guarded_ops_agent.tools.registry import ExecutionRecord

__all__ = ["AgentState", "RunStatus"]

from enum import StrEnum


class RunStatus(StrEnum):
    """Terminal-ish state of a run, as the API reports it.

    ``AWAITING_APPROVAL`` is the one a UI must handle specially: the run is
    real, durable, and paused. It is not an error and it is not finished.
    """

    RUNNING = "running"
    #: Policy allowed it (or the guard was off) and the tool ran.
    COMPLETED = "completed"
    #: A rule denied the action. The reply names the rule.
    REJECTED = "rejected"
    #: Paused on ``interrupt()``. Resumes when a human decides.
    AWAITING_APPROVAL = "awaiting_approval"
    #: An input guardrail refused before any model call.
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentState(TypedDict, total=False):
    """State threaded through the graph.

    ``total=False`` because nodes return partial updates — a node that only sets
    ``proposal`` returns exactly that key, and LangGraph merges it.
    """

    # -- inputs (set once at invocation) -------------------------------------
    conversation_id: str
    run_id: str
    user_message: str
    customer_id: str | None
    #: The ablation flag, carried in state rather than read from config inside a
    #: node. A node that reaches for global config is a node you cannot test
    #: both ways, and this flag existing in the trail is what makes an ablation
    #: run attributable after the fact.
    policy_enabled: bool

    # -- produced by nodes ---------------------------------------------------
    #: What the model proposed. None until `propose` runs.
    proposal: AgentProposal | None
    #: What the policy engine decided. None until `decide` runs.
    decision: PolicyDecision | None
    #: The human's verdict. None unless the run was escalated and resumed.
    approval: ApprovalDecision | None
    #: The id of the queued approval request, for the API to hand to a UI.
    approval_id: str | None
    #: What the tool did. None if nothing executed.
    execution: ExecutionRecord | None
    #: Rules broken by the executed action. Always computed, both arms.
    violations: list[Violation]
    #: The customer-facing reply.
    reply: str | None
    status: RunStatus
    error: str | None

    #: Audit events accumulated across nodes.
    #:
    #: `operator.add` is the reducer: each node RETURNS ONLY THE EVENTS IT
    #: ADDED, and LangGraph concatenates. Returning the whole list from each
    #: node would re-append everything already there and duplicate the trail on
    #: every step — a bug that only shows up as a quietly wrong audit record.
    audit: Annotated[list[AuditEvent], operator.add]
