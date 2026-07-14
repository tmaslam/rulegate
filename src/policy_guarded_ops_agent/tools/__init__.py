"""Tools: the only code allowed to touch the billing API.

Every tool returns a validated Pydantic model. Nothing here parses model output,
and nothing here decides whether an action is *allowed* — by the time a tool
runs, ``policy/`` has already said yes (or the ablation explicitly turned the
guard off and said so in the trail).
"""

from __future__ import annotations
