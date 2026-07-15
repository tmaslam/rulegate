"""FastAPI dependency providers.

The runtime is built once in the lifespan and stashed on ``app.state``. This
module is the single accessor, so routes never reach into ``app.state`` directly
and a test can override :func:`get_runtime` to inject a runtime wired to the
fake provider and an in-memory database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

# CRITICAL — `Request` must stay a RUNTIME import; do not "fix" this TC002.
#
# Same class of hazard as the pydantic/langgraph ones in pyproject.toml, and this
# one fails silently rather than loudly. FastAPI resolves this signature at route
# registration to decide what `request` is. Behind `if TYPE_CHECKING:` the name is
# unresolvable, so FastAPI stops recognising it as the Request object and demotes
# it to a **query parameter** — every call to a route depending on get_runtime
# then returns `422 {"loc": ["query", "request"], "msg": "Field required"}`
# instead of running. No NameError, no import error: just a dead endpoint.
#
# Verified on this venv's fastapi: identical dependency with the import under
# TYPE_CHECKING returns 422; with the runtime import below it returns 200.
from fastapi import Request  # noqa: TC002

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
