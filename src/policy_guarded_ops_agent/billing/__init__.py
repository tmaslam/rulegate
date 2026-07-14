"""The mock billing/subscription API the agent operates on.

Mock, but not a stub: it enforces its own integrity invariants (a charge cannot
be over-refunded, a canceled subscription cannot be changed) independently of
the policy engine. Those are *system* constraints, distinct from the *business*
rules in ``policy/`` — and keeping them separate is deliberate. A policy engine
that is the only thing standing between a bug and a double refund is not a
demonstration of anything.
"""

from __future__ import annotations
