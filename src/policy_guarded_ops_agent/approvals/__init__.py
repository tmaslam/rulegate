"""The human-in-the-loop approval queue backing LangGraph's ``interrupt()``.

Durability is the whole point. When the graph pauses on a high-value action, two
things must survive a process restart independently:

1. **The graph's own state** — LangGraph's checkpointer handles this
   (``storage/checkpointer.py``).
2. **The human's work queue** — this package. A reviewer needs to see pending
   items in a UI, which means they must be *queryable rows*, not a coroutine
   suspended in some worker's memory.

Losing either one strands a customer's refund forever, so neither lives in RAM.
"""

from __future__ import annotations
