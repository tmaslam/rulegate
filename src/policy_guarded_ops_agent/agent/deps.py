"""Everything the graph needs, injected rather than imported.

Why a container instead of module-level singletons
--------------------------------------------------
Every collaborator here has a test double or an offline configuration: the
gateway takes a fake backend, the billing API takes a frozen clock, the database
is SQLite in a temp dir. If nodes reached for module-level globals instead, none
of that would be substitutable and the suite would need real infrastructure to
run — which is the thing this repo promises it does not.

The clock is in here for the same reason. "45 days ago" must mean the same thing
in a test as in production, and one injected clock shared by the billing API, the
rules and the audit trail is what stops a decision being stamped before the fact
it reasoned about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from policy_guarded_ops_agent.approvals.queue import ApprovalQueue
    from policy_guarded_ops_agent.audit.store import AuditStore
    from policy_guarded_ops_agent.billing.api import Clock, MockBillingAPI
    from policy_guarded_ops_agent.config import Settings
    from policy_guarded_ops_agent.guardrails.base import GuardrailPipeline
    from policy_guarded_ops_agent.llm.gateway import Gateway
    from policy_guarded_ops_agent.policy.engine import PolicyEngine
    from policy_guarded_ops_agent.policy.violations import ViolationDetector
    from policy_guarded_ops_agent.tools.registry import ToolRegistry

__all__ = ["AgentDeps"]


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """The graph's collaborators.

    Frozen: swapping a dependency mid-run would make the audit trail describe a
    system that no longer exists.
    """

    #: LLM access. Proposes only — it decides nothing.
    gateway: Gateway
    billing: MockBillingAPI
    tools: ToolRegistry
    #: The gate.
    engine: PolicyEngine
    #: The auditor. Same rules as the gate, no power to stop anything.
    detector: ViolationDetector
    guardrails: GuardrailPipeline
    approvals: ApprovalQueue
    audit: AuditStore
    settings: Settings
    #: One clock, shared by everything that stamps a record.
    clock: Clock
