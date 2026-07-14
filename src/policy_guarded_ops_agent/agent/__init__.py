"""The typed LangGraph agent that wires the model to the guarded tools.

The graph is where the LLM and the policy engine meet, and the shape of it is
the argument this repo makes:

    guard_input -> propose (LLM) -> decide (CODE) -> [execute | human_review] -> respond

``propose`` is the only node that talks to a model. ``decide`` is the only node
that determines whether anything happens. They are different nodes, in different
packages, and the model has no edge back into the decision.
"""

from __future__ import annotations
