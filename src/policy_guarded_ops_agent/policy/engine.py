"""The policy engine: evaluate every applicable rule, fold to one decision.

Deliberately boring. It runs rules, folds their effects by strictness, and
records what happened. There is no cleverness to review, which is the point —
the interesting judgement lives in ``rules.py`` where it can be read.

No I/O lives here. The engine is handed a fully-resolved
:class:`~.models.PolicyContext`; fetching the facts is the caller's job (see
``agent/context.py``). That keeps ``policy/`` free of ``await``, which is what
makes the whole package testable at microsecond speed with no fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog
from pydantic import BaseModel, ConfigDict

from policy_guarded_ops_agent.policy.models import (
    Effect,
    PolicyDecision,
    RuleId,
    RuleOutcome,
    strictest,
)
from policy_guarded_ops_agent.policy.rules import default_rules

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from policy_guarded_ops_agent.domain.actions import ProposedAction
    from policy_guarded_ops_agent.policy.models import PolicyContext
    from policy_guarded_ops_agent.policy.rules import Rule

__all__ = ["PolicyEngine", "RuleDescription"]

log: Final = structlog.get_logger(__name__)


class RuleDescription(BaseModel):
    """A rule's public description, for the ``GET /policy/rules`` endpoint.

    The rules are the product's contract with its user, so they are queryable at
    runtime rather than only documented in a README that can drift.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: RuleId
    description: str


class PolicyEngine:
    """Approves, rejects or escalates a proposed action. No LLM involved.

    Folding: **the strictest verdict wins.** A single DENY beats any number of
    ALLOWs, and an ESCALATE beats an ALLOW. Rules never vote and never average —
    a rule that objects is not outnumbered, because that is not what a business
    rule means.

    Example::

        engine = PolicyEngine()
        decision = engine.evaluate(action, ctx)
        if decision.effect is Effect.DENY:
            reply(decision.rationale)  # names the exact rule that fired
    """

    def __init__(self, *, rules: Sequence[Rule] | None = None) -> None:
        self._rules: tuple[Rule, ...] = tuple(rules) if rules is not None else default_rules()
        duplicates = self._duplicate_ids()
        if duplicates:
            # Two rules sharing an id would make the audit trail ambiguous about
            # which one fired, which defeats the purpose of recording it.
            msg = f"duplicate rule ids in engine: {sorted(duplicates)}"
            raise ValueError(msg)

    def _duplicate_ids(self) -> set[RuleId]:
        seen: set[RuleId] = set()
        duplicates: set[RuleId] = set()
        for rule in self._rules:
            if rule.rule_id in seen:
                duplicates.add(rule.rule_id)
            seen.add(rule.rule_id)
        return duplicates

    @property
    def rules(self) -> tuple[Rule, ...]:
        """The configured rules, in evaluation order."""
        return self._rules

    def describe(self) -> tuple[RuleDescription, ...]:
        """Every rule's id and description, for the API and the docs."""
        return tuple(
            RuleDescription(rule_id=r.rule_id, description=r.description) for r in self._rules
        )

    def evaluate(
        self,
        action: ProposedAction,
        ctx: PolicyContext,
        *,
        now: datetime | None = None,
    ) -> PolicyDecision:
        """Evaluate every applicable rule against ``action``.

        Args:
            action: The action the model proposed.
            ctx: Fully-resolved facts. Rules read nothing else.
            now: Decision timestamp for the record. Defaults to ``ctx.now`` so
                the decision is stamped with the same instant the rules reasoned
                about — using a second clock read here would let the record and
                the reasoning disagree.

        Returns:
            A decision naming every rule that applied and the one that decided.
        """
        outcomes: list[RuleOutcome] = []
        for rule in self._rules:
            outcome = rule.evaluate(action, ctx)
            if outcome is not None:
                outcomes.append(outcome)

        effect = strictest(tuple(o.effect for o in outcomes))
        decision = PolicyDecision(
            effect=effect,
            action=action,
            evaluated=tuple(outcomes),
            decided_at=now if now is not None else ctx.now,
            policy_enabled=True,
        )
        if effect is not Effect.ALLOW:
            # The exact rule that fired, every time, at the moment it fires.
            log.info(
                "policy_decision",
                effect=str(effect),
                action=str(action.action),
                deciding_rule=str(decision.deciding_rule),
                rationale=decision.rationale,
            )
        return decision

    @staticmethod
    def bypass(action: ProposedAction, ctx: PolicyContext) -> PolicyDecision:
        """Produce an ALLOW decision **without evaluating any rule**.

        This is the OFF arm of the policy ablation, and it is a named method
        rather than an ``if`` inside :meth:`evaluate` for two reasons:

        1. The resulting decision is stamped ``policy_enabled=False`` and
           carries an empty ``evaluated`` tuple, so the audit trail can never
           be read as "the rules checked this and were happy". It says, in the
           record itself, that nothing was checked.
        2. It cannot be reached by accident. Bypassing the guard is an explicit
           call at one call site (``agent/nodes.py::decide``), not a flag that
           quietly changes what ``evaluate`` means.

        **Never enable this in production.** It exists to measure what the guard
        is worth.
        """
        log.warning(
            "policy_bypassed",
            action=str(action.action),
            reason="policy_enabled=False (ablation arm) — no rule was evaluated",
        )
        return PolicyDecision(
            effect=Effect.ALLOW,
            action=action,
            evaluated=(),
            decided_at=ctx.now,
            policy_enabled=False,
        )
