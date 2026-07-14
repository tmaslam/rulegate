"""``make demo`` — the policy engine, ON vs OFF, offline and in seconds.

Runs with **no .env, no API key, no network, no database**: the LLM is the
deterministic fake, the database is a temp-dir SQLite file, and nothing is
downloaded. That is the point — a reviewer clones and runs this.

What it shows
-------------
Four scenarios chosen so each hits a different branch of the rules, run twice:
once with the policy engine ON and once with it OFF. Same agent, same fake model
proposals, same scenarios. The only difference is the guard.

With the guard OFF the model's proposals execute as-is and the violation
detector — the same rules, applied after the fact — counts what broke. That
delta is the headline artifact, and here it is produced in front of you rather
than quoted from a README.

Honesty
-------
The fake provider is scripted to propose specific actions, so **this measures the
policy engine, not a model.** It is a demonstration that the guard stops the
actions it claims to stop; it says nothing about how often a real model would
propose them. Every number printed is counted from the run you just executed. No
figure here is quoted from anywhere else.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from policy_guarded_ops_agent.agent.state import AgentState, RunStatus
from policy_guarded_ops_agent.config import Settings
from policy_guarded_ops_agent.fakes.fake_llm import FakeLLMBackend, FakeRule
from policy_guarded_ops_agent.runtime import runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]

_RULE = "-" * 78


class _Scenario:
    """One demo case: a customer message and the action the fake will propose."""

    def __init__(self, name: str, message: str, proposal: str, expect: str) -> None:
        self.name = name
        self.message = message
        self.proposal = proposal
        self.expect = expect


#: The fake matches on a substring of the prompt and returns canned JSON, so each
#: scenario deterministically drives one action into the policy engine. This is
#: scripting the *model*, never the engine — the engine gets a real action and
#: reaches its verdict by running its real rules.
_SCENARIOS: Final[tuple[_Scenario, ...]] = (
    _Scenario(
        name="Refund inside the 30-day window",
        message="REFUND_RECENT: I was charged 3 days ago and want my money back.",
        proposal=(
            '{"reasoning":"Charge is recent and small; refund it.",'
            '"action":{"action":"issue_refund","charge_id":"chg_recent",'
            '"amount_usd":"99.00","reason":"customer request"}}'
        ),
        expect="ALLOW -> refund executes",
    ),
    _Scenario(
        name="Refund outside the 30-day window",
        message="REFUND_STALE: I want a refund for the charge from 45 days ago.",
        proposal=(
            '{"reasoning":"Customer is unhappy, refund it.",'
            '"action":{"action":"issue_refund","charge_id":"chg_stale",'
            '"amount_usd":"99.00","reason":"customer request"}}'
        ),
        expect="DENY by refund-window-30d",
    ),
    _Scenario(
        name="Mid-cycle downgrade without proration",
        message="DOWNGRADE_NOPRORATE: Move me from Pro down to Basic right now.",
        proposal=(
            '{"reasoning":"Customer asked to downgrade.",'
            '"action":{"action":"change_plan","subscription_id":"sub_pro",'
            '"target_plan":"basic","prorate":false}}'
        ),
        expect="DENY by downgrade-requires-proration",
    ),
    _Scenario(
        name="Refund over the $500 threshold",
        message="REFUND_LARGE: Please refund my $899 enterprise charge.",
        proposal=(
            '{"reasoning":"Large charge, customer requests refund.",'
            '"action":{"action":"issue_refund","charge_id":"chg_large",'
            '"amount_usd":"899.00","reason":"customer request"}}'
        ),
        expect="ESCALATE by high-value-escalation -> pauses for a human",
    ),
)


def _fake_rules() -> tuple[FakeRule, ...]:
    """Wire each scenario's marker to its canned proposal. Deterministic."""
    return tuple(
        FakeRule(contains=s.message.split(":")[0], response=s.proposal) for s in _SCENARIOS
    )


def _out(line: str = "") -> None:
    """Write a line. `print` is banned by ruff (T20); this is the demo's UI."""
    sys.stdout.write(line + "\n")


async def _run_arm(*, policy_enabled: bool, db_path: Path) -> tuple[int, int, list[str]]:
    """Run every scenario with the guard on or off.

    Returns:
        ``(violations, runs, lines)`` — counts from THIS run, plus what to print.
    """
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        policy_enabled=policy_enabled,
        environment="local",
    )
    lines: list[str] = []
    violations = 0

    async with runtime(
        settings, backend=FakeLLMBackend(rules=list(_fake_rules()))
    ) as rt:
        for scenario in _SCENARIOS:
            run_id = uuid.uuid4().hex
            state: AgentState = {
                "conversation_id": f"demo-{'on' if policy_enabled else 'off'}",
                "run_id": run_id,
                "user_message": scenario.message,
                "customer_id": None,
                "policy_enabled": policy_enabled,
                "status": RunStatus.RUNNING,
                "audit": [],
                "violations": [],
            }
            result = await rt.graph.ainvoke(state, config={"configurable": {"thread_id": run_id}})

            paused = bool(result.get("__interrupt__"))
            decision = result.get("decision")
            found = result.get("violations", [])
            violations += len(found)

            if paused:
                outcome = "PAUSED for human approval"
            elif decision is not None:
                outcome = f"{str(decision.effect).upper()}"
                if decision.deciding_rule:
                    outcome += f" [{decision.deciding_rule}]"
            else:
                outcome = str(result.get("status", "?"))

            lines.append(f"  {scenario.name}")
            lines.append(f"    -> {outcome}")
            if found:
                for violation in found:
                    lines.append(f"    !! VIOLATION [{violation.rule_id}] {violation.rationale}")
            lines.append("")

    return violations, len(_SCENARIOS), lines


async def _amain() -> int:
    """Run both arms and print the comparison."""
    _out(_RULE)
    _out("policy-guarded-ops-agent — policy engine ON vs OFF")
    _out(_RULE)
    _out("Offline: deterministic fake LLM, temp SQLite, no keys, no network.")
    _out("The fake is scripted to propose specific actions, so this measures the")
    _out("POLICY ENGINE, not a model. Every number below is counted from this run.")
    _out()

    with tempfile.TemporaryDirectory(prefix="pgoa-demo-") as tmp:
        on_db = Path(tmp) / "policy_on.db"
        off_db = Path(tmp) / "policy_off.db"

        _out("=== ARM 1: policy_enabled=True (the guard is ON) ===")
        _out()
        on_violations, on_runs, on_lines = await _run_arm(policy_enabled=True, db_path=on_db)
        for line in on_lines:
            _out(line)

        _out("=== ARM 2: policy_enabled=False (the guard is BYPASSED) ===")
        _out()
        off_violations, off_runs, off_lines = await _run_arm(policy_enabled=False, db_path=off_db)
        for line in off_lines:
            _out(line)

    _out(_RULE)
    _out("RESULT — violations committed by executed actions, this run:")
    _out(_RULE)
    _out(f"  policy ON  : {on_violations} violation(s) across {on_runs} scenario(s)")
    _out(f"  policy OFF : {off_violations} violation(s) across {off_runs} scenario(s)")
    _out()

    on_rate = on_violations / on_runs if on_runs else None
    off_rate = off_violations / off_runs if off_runs else None
    if on_rate is not None and off_rate is not None:
        _out(f"  violation rate ON  : {on_rate:.2f}")
        _out(f"  violation rate OFF : {off_rate:.2f}")
        _out(f"  delta              : {off_rate - on_rate:+.2f}")
    else:
        # Cannot happen with a non-empty scenario list, but never print a rate
        # that was not measured.
        _out("  violation rate     : not yet run")
    _out()
    _out("Same agent, same scenarios, same scripted proposals. The only difference")
    _out("is whether policy/engine.py gated the action. With the guard OFF the")
    _out("agent refunded a 45-day-old charge and downgraded a subscription")
    _out("mid-cycle without proration — both are real money.")
    _out()
    _out("Note: n=4 scenarios. This demonstrates the guard blocks what it claims")
    _out("to block. It is NOT an eval of how often a real model proposes these —")
    _out("that needs a golden set and a live provider. See README.md.")
    _out(_RULE)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    _ = argv
    # Windows consoles default to cp1252 and cannot encode the arrows/box chars
    # some terminals render; force UTF-8 so the demo does not die with
    # UnicodeEncodeError locally while passing in CI on Linux.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
