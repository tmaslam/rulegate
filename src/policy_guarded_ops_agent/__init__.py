"""Policy-Guarded AI Operations Agent.

An agent that runs real customer-ops workflows over a mock billing/subscription
API and **provably** obeys the business rules — because the rules are not a
prompt.

The one idea this repo exists to demonstrate
--------------------------------------------
**The LLM proposes; deterministic code decides.**

``agent/`` asks a model for a *proposal* (a validated
:class:`~policy_guarded_ops_agent.domain.actions.ProposedAction`, never free
text, never regex-parsed JSON). ``policy/`` then approves, rejects or escalates
that proposal using plain Python — no model call, no prompt, no temperature. A
rejection always names the exact rule that fired.

That separation is the product, so it is the file layout:

* ``domain/``    — entities and the action union the model may propose.
* ``billing/``   — the mock billing API the tools sit on.
* ``policy/``    — the rules, the engine that gates on them, and the violation
                   detector that audits against them. **No LLM import may ever
                   appear in this package**; ``tests/test_policy_purity.py``
                   enforces that mechanically.
* ``tools/``     — side-effecting operations returning validated models.
* ``agent/``     — the typed LangGraph StateGraph that wires it together.
* ``audit/``     — the queryable trail of every decision, call and approval.
* ``approvals/`` — the human-in-the-loop queue backing ``interrupt()``.
* ``storage/``   — Neon/Postgres live, SQLite offline. Both paths work.
* ``api/``       — the FastAPI surface.

Copied in from the shared spine and never imported out of it: ``llm/``,
``obs/``, ``guardrails/``, ``fakes/``.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Kept in step with `version` in pyproject.toml.
__version__ = "0.1.0"
