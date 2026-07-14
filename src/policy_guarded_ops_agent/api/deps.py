"""FastAPI dependency providers.

The runtime is built once in the lifespan and stashed on ``app.state``. This
module is the single accessor, so routes never reach into ``app.state`` directly
and a test can override :func:`get_runtime` to inject a runtime wired to the
fake provider and an in-memory database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import Request

if TYPE_CHECKING:
    from policy_guarded_ops_agent.runtime import AgentRuntime

__all__ = ["RUNTIME_STATE_KEY", "get_runtime"]

#: Where the lifespan stores the runtime on `app.state`.
RUNTIME_STATE_KEY: Final = "agent_runtime"


def get_runtime(request: Request) -> AgentRuntime:
    """Return the process-wide agent runtime.

    Raises:
        RuntimeError: The lifespan did not run. This is a wiring bug — a request
            arriving before startup means the app was constructed without its
            lifespan (a common mistake when a test builds ``TestClient`` without
            entering the context), and failing loudly beats serving 500s from a
            half-built service.
    """
    runtime: AgentRuntime | None = getattr(request.app.state, RUNTIME_STATE_KEY, None)
    if runtime is None:
        msg = (
            "agent runtime is not initialised: the FastAPI lifespan has not run. "
            "Enter the app's lifespan (e.g. via httpx ASGITransport with "
            "asgi_lifespan.LifespanManager) before issuing requests."
        )
        raise RuntimeError(msg)
    return runtime
