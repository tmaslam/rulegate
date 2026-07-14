"""Detects rules broken by actions that were **actually executed**.

The measuring instrument
------------------------
This module is what turns "the policy engine works" from a claim into a number.

``engine.py`` is the *gate*: it runs before an action and can stop it.
This module is the *auditor*: it runs after an action executed and reports which
rules that action broke. It runs on **both** arms of the ablation — with the
gate on and with the gate off — which is the only reason the two arms are
comparable.

Both consume the identical rule objects from ``rules.py``. There is no second
"expected behaviour" definition to drift out of step with the first. If someone
weakens a rule to make the gate permissive, the auditor becomes permissive in
exactly the same way and the ablation delta collapses to zero — a change that is
visibly self-defeating rather than quietly flattering.

What a violation means
----------------------
* ``DENY`` rule fired but the action ran   -> :attr:`Severity.CRITICAL`. The
  action should never have happened.
* ``ESCALATE`` rule fired and no human approved -> :attr:`Severity.HIGH`. The
  action may well have been correct, but nobody with authority said so.

An ESCALATE rule that fired **and** was approved by a human is *not* a
violation: that is the escalation path working exactly as designed. Counting it
as a violation would make the policy-ON arm look broken precisely when it is
behaving best, and would be a fabricated number in the ablation table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog

from policy_guarded_ops_agent.policy.models import Effect, Severity, Violation
from policy_guarded_ops_agent.policy.rules import default_rules

if TYPE_CHECKING:
    from collections.abc import Sequence

    from policy_guarded_ops_agent.domain.actions import ProposedAction
    from policy_guarded_ops_agent.policy.models import PolicyContext
    from policy_guarded_ops_agent.policy.rules import Rule

__all__ = ["ViolationDetector"]

log: Final = structlog.get_logger(__name__)


class ViolationDetector:
    """Reports which rules an executed action broke.

    Independent of the engine by construction: it is handed the same rules and
    asks them the same question, but it asks *after* the fact and it has no
    power to stop anything. That asymmetry is what makes it a measurement.
    """

    def __init__(self, *, rules: Sequence[Rule] | None = None) -> None:
        self._rules: tuple[Rule, ...] = tuple(rules) if rules is not None else default_rules()

    @property
    def rules(self) -> tuple[Rule, ...]:
        """The rules this detector audits against."""
        return self._rules

    def detect(
        self,
        action: ProposedAction,
        ctx: PolicyContext,
        *,
        human_approved: bool = False,
    ) -> tuple[Violation, ...]:
        """Violations committed by executing ``action`` under ``ctx``.

        Args:
            action: The action that was executed.
            ctx: The facts **as they were before execution**. Passing
                post-execution facts would let a refund's own effect (the
                charge's balance dropping) hide the violation that the refund
                itself committed.
            human_approved: Whether a human explicitly approved this action. An
                approved escalation is not a violation.

        Returns:
            Every violation, empty when the action was legitimate.
        """
        violations: list[Violation] = []
        for rule in self._rules:
            outcome = rule.evaluate(action, ctx)
            if outcome is None or outcome.effect is Effect.ALLOW:
                continue
            if outcome.effect is Effect.ESCALATE and human_approved:
                # Approved escalation: the system worked. Not a violation.
                continue
            violations.append(
                Violation(
                    rule_id=outcome.rule_id,
                    severity=(
                        Severity.CRITICAL if outcome.effect is Effect.DENY else Severity.HIGH
                    ),
                    action_type=str(action.action),
                    rationale=outcome.rationale,
                    evidence=outcome.evidence,
                    detected_at=ctx.now,
                )
            )

        if violations:
            log.error(
                "policy_violation_detected",
                action=str(action.action),
                count=len(violations),
                rules=[str(v.rule_id) for v in violations],
            )
        return tuple(violations)
