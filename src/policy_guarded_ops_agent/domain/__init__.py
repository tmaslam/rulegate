"""Domain entities and the action vocabulary. No I/O, no LLM, no framework.

Deliberately free of re-exports: import from the module directly
(``from policy_guarded_ops_agent.domain.models import Customer``). A re-export
surface here would be a second thing to keep in step with the models for no gain.
"""

from __future__ import annotations
