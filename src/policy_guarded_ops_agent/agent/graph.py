"""The explicit StateGraph. The shape of this file is the argument.

::

    START
      |
    guard_input ---(blocked)---------------------------> respond
      |
    propose  (the only node that calls a model)
      |
      +------(failed)------------------------------> END
      |
    decide   (the only node that decides — plain Python)
      |
      +--- allow -----------------------------> execute ---> respond ---> END
      |
      +--- escalate --> human_review --+-approved-> execute
      |                                |
      |                                +-rejected-> respond
      |
      +--- deny ------------------------------------------> respond

Note what is *not* here: there is no edge from ``propose`` to ``execute``. The
model physically cannot reach a tool without passing through ``decide``. That is
not enforced by a prompt or a convention — it is enforced by the absence of an
edge, and ``tests/test_graph_topology.py`` asserts it stays absent.

A ``ToolNode``/ReAct loop would have made this shorter and would have destroyed
the property: in that design the model emits tool calls and the framework runs
them. Here the graph is explicit so the guard cannot be routed around.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

import structlog
from langgraph.graph import END, START, StateGraph

from policy_guarded_ops_agent.agent.nodes import AgentNodes
from policy_guarded_ops_agent.agent.state import AgentState, RunStatus
from policy_guarded_ops_agent.policy.models import Effect

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from policy_guarded_ops_agent.agent.deps import AgentDeps

__all__ = [
    "NODE_DECIDE",
    "NODE_EXECUTE",
    "NODE_GUARD_INPUT",
    "NODE_HUMAN_REVIEW",
    "NODE_PROPOSE",
    "NODE_RESPOND",
    "build_graph",
    "compile_graph",
]

log: Final = structlog.get_logger(__name__)

#: Node names as constants — the topology test and the tracing spans both key
#: off these, and a typo in a string edge is an error LangGraph only raises at
#: compile time.
NODE_GUARD_INPUT: Final = "guard_input"
NODE_PROPOSE: Final = "propose"
NODE_DECIDE: Final = "decide"
NODE_HUMAN_REVIEW: Final = "human_review"
NODE_EXECUTE: Final = "execute"
NODE_RESPOND: Final = "respond"


def _route_after_guard(state: AgentState) -> Literal["propose", "respond"]:
    """Blocked input skips the model entirely — and therefore costs nothing."""
    if state.get("status") is RunStatus.BLOCKED:
        return NODE_RESPOND
    return NODE_PROPOSE


#: LangGraph's END sentinel is declared as `END = sys.intern("__end__")` — a plain
#: `str`, not a `Literal`. Re-declaring it as a Literal lets the routers below keep
#: exhaustive return types that mypy can actually check, instead of widening them
#: to `str` and losing the check. The assertion keeps the two honest: if a future
#: langgraph renames the sentinel, this fails loudly at import rather than
#: producing a graph with a dangling edge.
_END: Final[Literal["__end__"]] = "__end__"
if _END != END:  # pragma: no cover — a tripwire, not a branch.
    _msg = f"langgraph's END sentinel changed from {_END!r} to {END!r}; update graph.py"
    raise RuntimeError(_msg)


def _route_after_propose(state: AgentState) -> Literal["decide", "__end__"]:
    """A failed proposal ends the run; its reply is already set."""
    if state.get("status") is RunStatus.FAILED:
        return _END
    return NODE_DECIDE


def _route_after_decide(state: AgentState) -> Literal["execute", "human_review", "respond"]:
    """The gate. Every effectful action leaves ``decide`` through one of these.

    A DENY goes to ``respond``, never to ``execute``. There is no fourth branch,
    and adding one is the only way to get an unchecked action to a tool.
    """
    decision = state.get("decision")
    if decision is None:  # pragma: no cover — decide always sets one.
        return NODE_RESPOND
    if decision.effect is Effect.DENY:
        return NODE_RESPOND
    if decision.effect is Effect.ESCALATE:
        return NODE_HUMAN_REVIEW
    return NODE_EXECUTE


def _route_after_review(state: AgentState) -> Literal["execute", "respond"]:
    """A human's rejection is a decision: the customer still gets an answer."""
    approval = state.get("approval")
    if approval is not None and approval.approved:
        return NODE_EXECUTE
    return NODE_RESPOND


def build_graph(deps: AgentDeps) -> StateGraph[AgentState, None, AgentState, AgentState]:
    """Build the uncompiled graph.

    Returned uncompiled so callers choose the checkpointer — the API binds a
    durable one, the topology test binds none.
    """
    nodes = AgentNodes(deps)
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)

    graph.add_node(NODE_GUARD_INPUT, nodes.guard_input)
    graph.add_node(NODE_PROPOSE, nodes.propose)
    graph.add_node(NODE_DECIDE, nodes.decide)
    graph.add_node(NODE_HUMAN_REVIEW, nodes.human_review)
    graph.add_node(NODE_EXECUTE, nodes.execute)
    graph.add_node(NODE_RESPOND, nodes.respond)

    graph.add_edge(START, NODE_GUARD_INPUT)
    graph.add_conditional_edges(NODE_GUARD_INPUT, _route_after_guard, [NODE_PROPOSE, NODE_RESPOND])
    graph.add_conditional_edges(NODE_PROPOSE, _route_after_propose, [NODE_DECIDE, END])
    graph.add_conditional_edges(
        NODE_DECIDE,
        _route_after_decide,
        # The explicit destination list is what the topology test reads. It is
        # also what makes an accidental `propose -> execute` edge impossible to
        # add without touching this line.
        [NODE_EXECUTE, NODE_HUMAN_REVIEW, NODE_RESPOND],
    )
    graph.add_conditional_edges(
        NODE_HUMAN_REVIEW, _route_after_review, [NODE_EXECUTE, NODE_RESPOND]
    )
    graph.add_edge(NODE_EXECUTE, NODE_RESPOND)
    graph.add_edge(NODE_RESPOND, END)
    return graph


def compile_graph(
    deps: AgentDeps,
    *,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Compile the graph against ``checkpointer``.

    A checkpointer is **required for HITL**: without one, ``interrupt()`` has
    nowhere to persist the paused state and the run cannot be resumed after the
    process restarts. It is optional here only so tests that never escalate can
    skip the setup.
    """
    return build_graph(deps).compile(checkpointer=checkpointer)
