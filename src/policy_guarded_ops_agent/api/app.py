"""The FastAPI application and its lifespan.

The lifespan owns the runtime: database pool, checkpointer connection and the
compiled graph, created once at startup and closed once at shutdown. Building
them per-request would exhaust Neon's free-tier connection ceiling almost
immediately and would re-open a SQLite handle on every call.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Final

import structlog
from fastapi import FastAPI

from policy_guarded_ops_agent import __version__
from policy_guarded_ops_agent.api.deps import RUNTIME_STATE_KEY
from policy_guarded_ops_agent.api.routes import router
from policy_guarded_ops_agent.config import Settings, get_settings
from policy_guarded_ops_agent.obs.tracing import configure_tracing, shutdown_tracing
from policy_guarded_ops_agent.runtime import runtime as build_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["create_app"]

log: Final = structlog.get_logger(__name__)

_DESCRIPTION: Final = """\
An agent that runs real customer-ops workflows over a mock billing API and
**provably** obeys the business rules — because the rules are not a prompt.

**The LLM proposes; deterministic code decides.** The model emits a validated
action; a rules engine written in plain Python approves, rejects or escalates
it. Every rejection names the exact rule that fired, and every decision, tool
call and human approval lands in a queryable audit trail.

* `POST /runs` — run the agent on a message. Carries the `policy_enabled`
  ablation flag.
* `GET /approvals`, `POST /approvals/{id}/decision` — the human-in-the-loop
  queue. High-value actions pause the graph durably and resume on approval,
  surviving a restart.
* `GET /ablation` — the headline artifact: violation rate with the guard on
  vs off, computed from the audit trail.

Runs with **no API key and no database**: no provider key selects a
deterministic fake, no `DATABASE_URL` selects a local SQLite file.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Injected in tests. Defaults to the process-wide settings read
            from the environment.
    """
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Own the runtime for the process's lifetime."""
        # Cheap and safe to call unconditionally: returns False and installs
        # nothing when LANGFUSE_* is unset. Call sites never branch on it.
        tracing_on = configure_tracing()
        async with AsyncExitStack() as stack:
            agent_runtime = await stack.enter_async_context(build_runtime(resolved))
            setattr(app.state, RUNTIME_STATE_KEY, agent_runtime)
            log.info(
                "service_started",
                version=__version__,
                environment=resolved.environment,
                tracing=tracing_on,
                policy_enabled=resolved.policy_enabled,
            )
            if not resolved.policy_enabled:
                # A service running with the guard off is an ablation
                # configuration. Say so loudly and repeatedly.
                log.warning(
                    "policy_disabled_at_startup",
                    note=(
                        "POLICY_ENABLED=false — the policy engine is BYPASSED for "
                        "every run. This is an experiment configuration and must "
                        "never be production."
                    ),
                )
            try:
                yield
            finally:
                shutdown_tracing()
                log.info("service_stopping")

    app = FastAPI(
        title="Policy-Guarded AI Operations Agent",
        description=_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "agent", "description": "Run the agent."},
            {
                "name": "policy",
                "description": "The deterministic rules, and the ON/OFF ablation.",
            },
            {"name": "approvals", "description": "Human-in-the-loop queue."},
            {"name": "audit", "description": "The queryable trail."},
            {"name": "ops", "description": "Health."},
        ],
    )
    app.include_router(router)
    return app
