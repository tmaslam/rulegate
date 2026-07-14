"""ASGI entry point.

``uvicorn policy_guarded_ops_agent.main:app`` — this exact path is what the
Dockerfile's CMD runs, and what a Hugging Face Space or Render free-tier service
points at. It exists as its own module so the import path in the Dockerfile does
not depend on the internal layout of ``api/``.

The app is created at import time because that is what an ASGI server expects to
find. Nothing expensive happens here: the database pool, checkpointer and graph
are all built in the lifespan, not at import, so importing this module is cheap
and side-effect-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from policy_guarded_ops_agent.api.app import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app"]

app: FastAPI = create_app()
