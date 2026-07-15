"""Builds a fully-wired agent from settings. The one place composition happens.

Used by the FastAPI lifespan, by ``demo.py`` and by the tests, so all three
exercise the *same* wiring. A demo that assembles the system differently from
production is a demo that proves nothing about production.

The zero-key path
-----------------
:func:`build_gateway` returns a gateway over the free-tier chain when a key is
present, and over the deterministic fake when none is. **The fake is not a
fallback bolted on for tests — it is the expected state of a fresh clone**, and
``build_default_chain()`` returning an empty tuple is a supported outcome, not an
error.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import structlog

from policy_guarded_ops_agent.agent.deps import AgentDeps
from policy_guarded_ops_agent.agent.graph import compile_graph
from policy_guarded_ops_agent.approvals.queue import ApprovalQueue
from policy_guarded_ops_agent.audit.store import AuditStore
from policy_guarded_ops_agent.billing.api import MockBillingAPI, utc_now
from policy_guarded_ops_agent.fakes.fake_llm import FakeLLMBackend, fake_provider_spec
from policy_guarded_ops_agent.guardrails.base import GuardrailPipeline
from policy_guarded_ops_agent.guardrails.ops import ops_input_filters, ops_output_filters
from policy_guarded_ops_agent.llm.gateway import Gateway, build_default_chain
from policy_guarded_ops_agent.policy.engine import PolicyEngine
from policy_guarded_ops_agent.policy.rules import default_rules
from policy_guarded_ops_agent.policy.violations import ViolationDetector
from policy_guarded_ops_agent.storage.checkpointer import build_checkpointer
from policy_guarded_ops_agent.storage.db import resolve_database
from policy_guarded_ops_agent.storage.session import Database
from policy_guarded_ops_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from policy_guarded_ops_agent.agent.state import AgentState
    from policy_guarded_ops_agent.billing.api import Clock
    from policy_guarded_ops_agent.config import Settings
    from policy_guarded_ops_agent.fakes.fake_llm import FakeRule
    from policy_guarded_ops_agent.llm.gateway import CompletionBackend

__all__ = ["AgentRuntime", "build_deps", "build_gateway", "runtime"]

log: Final = structlog.get_logger(__name__)


def build_gateway(
    settings: Settings,
    *,
    backend: CompletionBackend | None = None,
    fake_rules: tuple[FakeRule, ...] = (),
) -> Gateway:
    """Build the LLM gateway, degrading to the deterministic fake with no keys.

    Args:
        settings: Timeout/temperature/chain config.
        backend: Force a backend. Tests inject a fake with scripted behaviour.
        fake_rules: Rules for the auto-built fake, used only when no key is set
            and no explicit backend was given.

    Returns:
        A gateway. Never raises for a missing key — that is a supported state.
    """
    if backend is not None:
        return Gateway(
            chain=[fake_provider_spec()], backend=backend, timeout_s=settings.llm_timeout_s
        )

    env = (
        {"LLM_FALLBACK_CHAIN": settings.llm_fallback_chain} if settings.llm_fallback_chain else None
    )
    chain = build_default_chain(env)
    if chain:
        log.info("gateway_live", providers=[s.name for s in chain])
        return Gateway(chain=list(chain), timeout_s=settings.llm_timeout_s)

    # No keys: the expected state of a fresh clone.
    log.info(
        "gateway_fake",
        reason="no provider API key configured; using the deterministic fake",
    )
    return Gateway(
        chain=[fake_provider_spec()],
        backend=FakeLLMBackend(rules=list(fake_rules)),
        timeout_s=settings.llm_timeout_s,
    )


def build_deps(
    settings: Settings,
    *,
    database: Database,
    gateway: Gateway,
    clock: Clock = utc_now,
    billing: MockBillingAPI | None = None,
) -> AgentDeps:
    """Assemble the graph's dependencies.

    The engine and the detector are built from **one** call to
    :func:`~policy_guarded_ops_agent.policy.rules.default_rules`, so the gate and
    the auditor are provably the same rules — not two lists that happen to match
    today.
    """
    rules = default_rules(
        refund_window_days=settings.refund_window_days,
        escalation_threshold_usd=settings.escalation_threshold_usd,
    )
    api = billing if billing is not None else MockBillingAPI(clock=clock)
    return AgentDeps(
        gateway=gateway,
        billing=api,
        tools=ToolRegistry(api, clock=clock),
        engine=PolicyEngine(rules=rules),
        detector=ViolationDetector(rules=rules),
        guardrails=GuardrailPipeline(
            input_filters=ops_input_filters(max_chars=settings.max_input_chars),
            output_filters=ops_output_filters(),
        ),
        approvals=ApprovalQueue(database),
        audit=AuditStore(database),
        settings=settings,
        clock=clock,
    )


class AgentRuntime:
    """A live, wired agent: database, checkpointer, graph and deps."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        deps: AgentDeps,
        graph: CompiledStateGraph[AgentState, None, AgentState, AgentState],
        checkpointer: BaseCheckpointSaver[str],
    ) -> None:
        self.settings = settings
        self.database = database
        self.deps = deps
        self.graph = graph
        self.checkpointer = checkpointer


@asynccontextmanager
async def runtime(
    settings: Settings,
    *,
    backend: CompletionBackend | None = None,
    fake_rules: tuple[FakeRule, ...] = (),
    clock: Clock = utc_now,
    billing: MockBillingAPI | None = None,
) -> AsyncIterator[AgentRuntime]:
    """Stand up a complete agent for the lifetime of the context.

    Owns the database pool and the checkpointer connection, both of which must
    outlive every request and be closed exactly once. That is why this is a
    context manager and not a factory: a checkpointer whose connection has
    already been closed by an exited ``with`` block fails at the first interrupt,
    not at construction.

    Example::

        async with runtime(Settings()) as rt:
            result = await rt.graph.ainvoke(state, config)
    """
    config = resolve_database(settings.database_url)
    database = Database(config)
    await database.create_all()
    gateway = build_gateway(settings, backend=backend, fake_rules=fake_rules)
    deps = build_deps(settings, database=database, gateway=gateway, clock=clock, billing=billing)
    try:
        async with build_checkpointer(config) as checkpointer:
            graph = compile_graph(deps, checkpointer=checkpointer)
            log.info(
                "runtime_ready",
                backend=str(config.backend),
                policy_enabled=settings.policy_enabled,
            )
            yield AgentRuntime(
                settings=settings,
                database=database,
                deps=deps,
                graph=graph,
                checkpointer=checkpointer,
            )
    finally:
        await database.dispose()
