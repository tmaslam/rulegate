"""The FastAPI surface.

Everything the UI needs and nothing it does not. Three groups:

* **Run** the agent on a message (``POST /runs``).
* **Approve** a paused run (``GET/POST /approvals/...``) — the HITL loop.
* **Inspect** what happened (``GET /audit/...``, ``GET /policy/rules``,
  ``GET /ablation``).

Every response is a Pydantic model, so the OpenAPI schema at ``/docs`` is
generated from the same types the code enforces and cannot drift from them.
"""

from __future__ import annotations
