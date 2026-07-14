"""The business rules. Deterministic code — this is the product.

Every rule is a pure function of ``(action, PolicyContext)``. No I/O, no clock
read, no LLM, no ``await``. Given the same action and the same facts it returns
the same verdict on every machine, forever. That is what "provably obeys your
business rules" has to mean if it is going to mean anything: you can read the
rule, and you can test it exhaustively in microseconds.

The three headline rules from the brief:

===========================================  ====================================
Rule                                         Id
===========================================  ====================================
No refund more than 30 days after the charge  ``refund-window-30d``
No mid-cycle downgrade without proration      ``downgrade-requires-proration``
Anything over $500 needs a human              ``high-value-escalation``
===========================================  ====================================

plus two integrity rules (``entity-must-exist``, ``refund-within-balance``) that
exist because a policy engine which happily approves a refund against a charge
that does not exist is not guarding anything.

Adding a rule
-------------
Implement :class:`Rule`, give it a new :class:`~.models.RuleId`, and add it to
:func:`default_rules`. Return ``None`` when the rule has no opinion about the
action — do not return ALLOW, which would claim you checked something you did
not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from policy_guarded_ops_agent.domain.actions import (
    CancelSubscriptionAction,
    ChangePlanAction,
    IssueRefundAction,
    ProposedAction,
)
from policy_guarded_ops_agent.domain.models import PLAN_MONTHLY_USD, plan_rank
from policy_guarded_ops_agent.policy.models import (
    DEFAULT_ESCALATION_THRESHOLD_USD,
    DEFAULT_REFUND_WINDOW_DAYS,
    Effect,
    PolicyContext,
    RuleId,
    RuleOutcome,
)

if TYPE_CHECKING:
    from decimal import Decimal

__all__ = [
    "DowngradeRequiresProrationRule",
    "EntityMustExistRule",
    "HighValueEscalationRule",
    "RefundWindowRule",
    "RefundWithinBalanceRule",
    "Rule",
    "action_value_usd",
    "default_rules",
]


@runtime_checkable
class Rule(Protocol):
    """A single deterministic business rule.

    Implementations MUST be pure: no I/O, no clock, no randomness. The engine
    calls them in a tight loop and the audit trail records their verdicts as
    fact.
    """

    @property
    def rule_id(self) -> RuleId:
        """Stable id, recorded in the audit trail."""
        ...

    @property
    def description(self) -> str:
        """One-line statement of the rule, for docs and the /policy endpoint."""
        ...

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Verdict on ``action`` given ``ctx``, or ``None`` if not applicable."""
        ...


def action_value_usd(action: ProposedAction, ctx: PolicyContext) -> Decimal | None:
    """The monetary value at stake in ``action``, or ``None`` if not applicable.

    Defined per action type, and the definitions are judgement calls worth
    stating out loud rather than burying:

    * **Refund** — the refund amount. Money leaving the business right now.
    * **Plan change** — the *target* plan's monthly list price, i.e. the amount
      being committed to. Using the delta instead would let an
      ENTERPRISE-to-PRO move look like a $800 swing and escalate a de-escalation.
    * **Cancel** — the current plan's monthly price: the recurring revenue being
      given up.

    Read-only, reply and escalate actions have no value and return ``None``.
    """
    if isinstance(action, IssueRefundAction):
        return action.amount_usd
    if isinstance(action, ChangePlanAction):
        return PLAN_MONTHLY_USD[action.target_plan]
    if isinstance(action, CancelSubscriptionAction):
        return ctx.subscription.monthly_price_usd if ctx.subscription is not None else None
    return None


class EntityMustExistRule:
    """The charge/subscription an effectful action names must exist.

    Runs first. Without it every downstream rule would have to defend itself
    against ``ctx.charge is None``, and the one that forgot would approve a
    refund against a charge nobody can find.
    """

    @property
    def rule_id(self) -> RuleId:
        """Stable id."""
        return RuleId.ENTITY_MUST_EXIST

    @property
    def description(self) -> str:
        """One-line statement."""
        return "An effectful action must reference an existing charge or subscription."

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Deny when the referenced entity was not resolvable."""
        if isinstance(action, IssueRefundAction):
            if ctx.charge is None:
                return self._deny("charge", action.charge_id)
            return self._allow(f"charge {action.charge_id} exists")
        if isinstance(action, ChangePlanAction | CancelSubscriptionAction):
            if ctx.subscription is None:
                return self._deny("subscription", action.subscription_id)
            return self._allow(f"subscription {action.subscription_id} exists")
        return None

    def _deny(self, kind: str, entity_id: str) -> RuleOutcome:
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.DENY,
            rationale=f"{kind} {entity_id!r} does not exist, so the action cannot be validated",
            evidence={"kind": kind, "entity_id": entity_id, "resolved": "false"},
        )

    def _allow(self, detail: str) -> RuleOutcome:
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.ALLOW,
            rationale=detail,
            evidence={"resolved": "true"},
        )


class RefundWindowRule:
    """No refund more than ``window_days`` after the charge settled.

    The headline rule. Compares instants, not calendar days: a charge 30.5 days
    old is outside a 30-day window. Rounding to whole days here would quietly
    widen the window by up to 24 hours, which over a year is a lot of refunds
    that the policy says do not exist.
    """

    def __init__(self, *, window_days: int = DEFAULT_REFUND_WINDOW_DAYS) -> None:
        self._window_days = window_days

    @property
    def rule_id(self) -> RuleId:
        """Stable id."""
        return RuleId.REFUND_WINDOW

    @property
    def description(self) -> str:
        """One-line statement."""
        return f"No refund more than {self._window_days} days after the charge date."

    @property
    def window_days(self) -> int:
        """The configured window."""
        return self._window_days

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Deny a refund whose charge is older than the window."""
        if not isinstance(action, IssueRefundAction) or ctx.charge is None:
            return None
        age_days = ctx.charge.age_days(ctx.now)
        evidence = {
            "charge_id": ctx.charge.id,
            "charge_age_days": f"{age_days:.4f}",
            "window_days": str(self._window_days),
            "charged_at": ctx.charge.charged_at.isoformat(),
        }
        if age_days > self._window_days:
            return RuleOutcome(
                rule_id=self.rule_id,
                effect=Effect.DENY,
                rationale=(
                    f"charge {ctx.charge.id} settled {age_days:.1f} days ago, which is "
                    f"outside the {self._window_days}-day refund window"
                ),
                evidence=evidence,
            )
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.ALLOW,
            rationale=(
                f"charge {ctx.charge.id} is {age_days:.1f} days old, inside the "
                f"{self._window_days}-day window"
            ),
            evidence=evidence,
        )


class RefundWithinBalanceRule:
    """A refund may not exceed what is left on the charge.

    Integrity rule. The billing API enforces the same invariant and would raise;
    this rule exists so the customer gets a *named refusal* instead of a 500,
    and so the trail records a decision rather than a crash.
    """

    @property
    def rule_id(self) -> RuleId:
        """Stable id."""
        return RuleId.REFUND_WITHIN_BALANCE

    @property
    def description(self) -> str:
        """One-line statement."""
        return "A refund may not exceed the charge's remaining refundable balance."

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Deny a refund larger than the charge's remaining balance."""
        if not isinstance(action, IssueRefundAction) or ctx.charge is None:
            return None
        refundable = ctx.charge.refundable_usd
        evidence = {
            "charge_id": ctx.charge.id,
            "requested_usd": str(action.amount_usd),
            "refundable_usd": str(refundable),
            "already_refunded_usd": str(ctx.charge.refunded_usd),
        }
        if action.amount_usd > refundable:
            return RuleOutcome(
                rule_id=self.rule_id,
                effect=Effect.DENY,
                rationale=(
                    f"requested refund of ${action.amount_usd} exceeds the ${refundable} "
                    f"still refundable on charge {ctx.charge.id} "
                    f"(${ctx.charge.refunded_usd} already refunded of ${ctx.charge.amount_usd})"
                ),
                evidence=evidence,
            )
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.ALLOW,
            rationale=f"${action.amount_usd} is within the ${refundable} refundable balance",
            evidence=evidence,
        )


class DowngradeRequiresProrationRule:
    """No mid-cycle plan downgrade without proration.

    The second headline rule, and the subtlest. All three conditions must hold
    for it to deny:

    1. the target plan ranks **below** the current plan (a downgrade),
    2. ``now`` is **strictly inside** the current billing period (mid-cycle), and
    3. ``prorate`` is **False**.

    An upgrade is unaffected. A downgrade at the period boundary is a renewal
    change, not a mid-cycle one. A prorated downgrade is exactly the thing the
    rule is asking for and passes. The rule fires only on the case that actually
    takes money from the customer: they paid for the richer plan through the end
    of the period, and dropping them without a credit keeps the difference.
    """

    @property
    def rule_id(self) -> RuleId:
        """Stable id."""
        return RuleId.DOWNGRADE_REQUIRES_PRORATION

    @property
    def description(self) -> str:
        """One-line statement."""
        return "No mid-cycle plan downgrade without proration."

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Deny an unprorated mid-cycle downgrade."""
        if not isinstance(action, ChangePlanAction) or ctx.subscription is None:
            return None
        subscription = ctx.subscription
        current_rank = plan_rank(subscription.plan)
        target_rank = plan_rank(action.target_plan)
        is_downgrade = target_rank < current_rank
        mid_cycle = subscription.is_mid_cycle(ctx.now)
        evidence = {
            "subscription_id": subscription.id,
            "current_plan": str(subscription.plan),
            "target_plan": str(action.target_plan),
            "is_downgrade": str(is_downgrade).lower(),
            "mid_cycle": str(mid_cycle).lower(),
            "prorate": str(action.prorate).lower(),
            "period_end": subscription.current_period_end.isoformat(),
        }
        if is_downgrade and mid_cycle and not action.prorate:
            return RuleOutcome(
                rule_id=self.rule_id,
                effect=Effect.DENY,
                rationale=(
                    f"downgrading {subscription.id} from {subscription.plan} to "
                    f"{action.target_plan} mid-cycle (period ends "
                    f"{subscription.current_period_end.date().isoformat()}) requires "
                    f"proration, but prorate=False"
                ),
                evidence=evidence,
            )
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.ALLOW,
            rationale=self._why_allowed(
                is_downgrade=is_downgrade, mid_cycle=mid_cycle, prorate=action.prorate
            ),
            evidence=evidence,
        )

    @staticmethod
    def _why_allowed(*, is_downgrade: bool, mid_cycle: bool, prorate: bool) -> str:
        """Say which condition spared the action. Specific beats generic in a trail."""
        if not is_downgrade:
            return "plan change is not a downgrade"
        if not mid_cycle:
            return "downgrade is at a period boundary, not mid-cycle"
        if prorate:
            return "mid-cycle downgrade is prorated"
        # Unreachable: !deny implies one of the above held. Kept total so a future
        # edit to the deny condition cannot silently produce a wrong rationale.
        return "downgrade permitted"


class HighValueEscalationRule:
    """Anything over the value threshold needs a human.

    The third headline rule. Escalates rather than denies: a $900 refund may be
    perfectly correct, but not on an agent's say-so alone. This is the rule that
    drives the HITL ``interrupt()`` — see ``agent/graph.py``.

    Strictly greater than: exactly $500.00 passes, $500.01 escalates. Stated
    because "over $500" is ambiguous in English and must not be ambiguous here.
    """

    def __init__(self, *, threshold_usd: Decimal = DEFAULT_ESCALATION_THRESHOLD_USD) -> None:
        self._threshold = threshold_usd

    @property
    def rule_id(self) -> RuleId:
        """Stable id."""
        return RuleId.HIGH_VALUE_ESCALATION

    @property
    def description(self) -> str:
        """One-line statement."""
        return f"Any action worth more than ${self._threshold} requires human approval."

    @property
    def threshold_usd(self) -> Decimal:
        """The configured threshold."""
        return self._threshold

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> RuleOutcome | None:
        """Escalate an effectful action whose value exceeds the threshold."""
        if not action.action.is_effectful:
            return None
        value = action_value_usd(action, ctx)
        if value is None:
            return None
        evidence = {
            "action_value_usd": str(value),
            "threshold_usd": str(self._threshold),
            "action_type": str(action.action),
        }
        if value > self._threshold:
            return RuleOutcome(
                rule_id=self.rule_id,
                effect=Effect.ESCALATE,
                rationale=(
                    f"{action.action} is worth ${value}, over the ${self._threshold} "
                    f"threshold, so it needs human approval"
                ),
                evidence=evidence,
            )
        return RuleOutcome(
            rule_id=self.rule_id,
            effect=Effect.ALLOW,
            rationale=f"${value} is within the ${self._threshold} auto-approval threshold",
            evidence=evidence,
        )


#: Evaluation order. Existence first (everything else depends on the entity
#: being resolvable), then the domain rules, then escalation last so a DENY from
#: a domain rule is what a reviewer sees rather than an escalation for an action
#: that was never legal.
_DEFAULT_ORDER: Final = (
    RuleId.ENTITY_MUST_EXIST,
    RuleId.REFUND_WINDOW,
    RuleId.REFUND_WITHIN_BALANCE,
    RuleId.DOWNGRADE_REQUIRES_PRORATION,
    RuleId.HIGH_VALUE_ESCALATION,
)


def default_rules(
    *,
    refund_window_days: int = DEFAULT_REFUND_WINDOW_DAYS,
    escalation_threshold_usd: Decimal = DEFAULT_ESCALATION_THRESHOLD_USD,
) -> tuple[Rule, ...]:
    """The rule set the service runs with.

    Returned in :data:`_DEFAULT_ORDER`. Order does not change the *decision*
    (the engine folds by strictness, not by position) but it does change the
    order of the audit trail, which humans read top-down.
    """
    rules: tuple[Rule, ...] = (
        EntityMustExistRule(),
        RefundWindowRule(window_days=refund_window_days),
        RefundWithinBalanceRule(),
        DowngradeRequiresProrationRule(),
        HighValueEscalationRule(threshold_usd=escalation_threshold_usd),
    )
    by_id = {r.rule_id: r for r in rules}
    return tuple(by_id[rule_id] for rule_id in _DEFAULT_ORDER)
