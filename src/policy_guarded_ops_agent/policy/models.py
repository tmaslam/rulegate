"""Policy value types: rule identity, effects, facts, decisions, violations.

Every type here is frozen and serialisable, because all of them end up in the
audit trail. A decision you cannot store and re-read later is not an audit
trail — it is a log line.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from policy_guarded_ops_agent.domain.actions import ProposedAction
from policy_guarded_ops_agent.domain.models import Charge, Customer, Subscription

__all__ = [
    "DEFAULT_ESCALATION_THRESHOLD_USD",
    "DEFAULT_REFUND_WINDOW_DAYS",
    "Effect",
    "PolicyContext",
    "PolicyDecision",
    "RuleId",
    "RuleOutcome",
    "Severity",
    "Violation",
]

#: The three business rules the README promises, as constants rather than magic
#: numbers buried in a conditional. Overridable per-engine; these are the
#: defaults the demo and the eval scenarios use.
DEFAULT_REFUND_WINDOW_DAYS: Final = 30
DEFAULT_ESCALATION_THRESHOLD_USD: Final = Decimal("500.00")


class RuleId(StrEnum):
    """Stable identifier for every rule.

    Stable because these strings are the audit trail's primary key for "why".
    Dashboards, tests and the ablation report all group by them; renaming one
    silently rewrites history, so treat them as a published interface.
    """

    #: The referenced charge/subscription does not exist.
    ENTITY_MUST_EXIST = "entity-must-exist"
    #: No refund more than N days after the charge settled.
    REFUND_WINDOW = "refund-window-30d"
    #: A refund cannot exceed what is left on the charge.
    REFUND_WITHIN_BALANCE = "refund-within-balance"
    #: No mid-cycle downgrade without proration.
    DOWNGRADE_REQUIRES_PRORATION = "downgrade-requires-proration"
    #: Anything over the value threshold needs a human.
    HIGH_VALUE_ESCALATION = "high-value-escalation"


class Effect(StrEnum):
    """What a rule (or a decision) says should happen.

    Ordered by severity via :data:`_EFFECT_PRECEDENCE`. When rules disagree the
    strictest wins — a DENY is never overridden by an ALLOW from a rule that
    simply had nothing to say.
    """

    ALLOW = "allow"
    ESCALATE = "escalate"
    DENY = "deny"


#: Higher binds tighter. DENY beats ESCALATE beats ALLOW.
_EFFECT_PRECEDENCE: Final[dict[Effect, int]] = {
    Effect.ALLOW: 0,
    Effect.ESCALATE: 1,
    Effect.DENY: 2,
}


def strictest(effects: tuple[Effect, ...]) -> Effect:
    """Fold effects to the strictest one. Empty input allows."""
    return max(effects, key=lambda e: _EFFECT_PRECEDENCE[e], default=Effect.ALLOW)


class Severity(StrEnum):
    """How bad a detected violation is."""

    #: An action that a rule would have DENIED was executed anyway. Money moved
    #: that should not have.
    CRITICAL = "critical"
    #: An action that required human approval was executed without it.
    HIGH = "high"


class PolicyContext(BaseModel):
    """The facts a rule is allowed to see.

    Resolved by the engine's caller **before** evaluation, so that every rule is
    a pure function of data with no I/O of its own. That is what makes the rules
    trivially testable, instantly fast, and impossible to make flaky — and it is
    why ``rules.py`` contains no ``await``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Evaluation instant. Injected, never read from the system clock inside a
    #: rule, so "45 days ago" means the same thing in a test as in production.
    now: datetime
    customer: Customer | None = None
    subscription: Subscription | None = None
    charge: Charge | None = None


class RuleOutcome(BaseModel):
    """One rule's verdict on one action.

    Produced only by rules that **applied**. A rule with nothing to say about an
    action returns ``None`` and never appears in the trail, which keeps the
    audit record about what was actually considered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: RuleId
    effect: Effect
    #: Human-readable and **specific**: contains the actual numbers that drove
    #: the verdict ("charge is 45.0 days old, limit is 30"), not a restatement
    #: of the rule's name. This string is what a reviewer reads at 2am.
    rationale: str
    #: Machine-readable facts behind the verdict, for querying the trail.
    evidence: Mapping[str, str] = Field(default_factory=dict)

    @property
    def fired(self) -> bool:
        """Whether this rule changed the outcome (i.e. did not simply allow)."""
        return self.effect is not Effect.ALLOW


class PolicyDecision(BaseModel):
    """The engine's verdict on one proposed action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: Effect
    action: ProposedAction
    #: Every rule that applied, in evaluation order — including the ones that
    #: allowed. "Which rules were even considered" is a question an auditor will
    #: ask, and the answer must not be reconstructed from memory.
    evaluated: tuple[RuleOutcome, ...] = ()
    decided_at: datetime
    #: False when the engine was bypassed for the ablation. A decision recorded
    #: with this False is NOT evidence the action was checked.
    policy_enabled: bool = True

    @property
    def fired(self) -> tuple[RuleOutcome, ...]:
        """Rules that denied or escalated, strictest first."""
        return tuple(
            sorted(
                (o for o in self.evaluated if o.fired),
                key=lambda o: _EFFECT_PRECEDENCE[o.effect],
                reverse=True,
            )
        )

    @property
    def deciding_rule(self) -> RuleId | None:
        """The rule that produced :attr:`effect`, or ``None`` when nothing fired.

        This is the answer to "which rule stopped my refund?" and it is why
        rules carry stable ids.
        """
        fired = self.fired
        return fired[0].rule_id if fired else None

    @property
    def rationale(self) -> str:
        """Why the decision came out this way, quoting the deciding rule."""
        fired = self.fired
        if not fired:
            return "no rule objected"
        return "; ".join(f"[{o.rule_id}] {o.rationale}" for o in fired)

    @property
    def allowed(self) -> bool:
        """Whether the action may execute right now with no further gating."""
        return self.effect is Effect.ALLOW

    @property
    def requires_approval(self) -> bool:
        """Whether the action needs a human before it may execute."""
        return self.effect is Effect.ESCALATE


class Violation(BaseModel):
    """A rule broken by an action that was **actually executed**.

    The unit of the policy-ON/OFF ablation. Produced by ``violations.py`` from
    the same rules the engine gates on, and recorded regardless of whether the
    engine was enabled — which is precisely what lets the two arms be compared.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: RuleId
    severity: Severity
    action_type: str
    rationale: str
    evidence: Mapping[str, str] = Field(default_factory=dict)
    detected_at: datetime
