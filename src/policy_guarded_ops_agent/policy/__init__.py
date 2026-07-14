"""The policy layer: deterministic business rules, in code, not in a prompt.

**This package must never import an LLM, a prompt, or a network client.** That
is not a style preference — it is the claim the whole repo makes, and
``tests/test_policy_purity.py`` asserts it mechanically by walking this
package's imports. If that test ever fails, the product is gone: a rule that a
model can be talked out of is not a rule.

Three modules, three jobs:

* ``rules.py``      — the rules themselves. Pure functions of (action, facts).
* ``engine.py``     — evaluates every applicable rule and folds the outcomes
                      into one decision. This is the *gate*.
* ``violations.py`` — evaluates the same rules against actions that were
                      **already executed**. This is the *auditor*, and it runs
                      whether or not the gate was switched on. It is what makes
                      the policy-ON/OFF ablation a measurement rather than an
                      assertion.

One rule set, two consumers. The auditor cannot drift from the gate because
there is nothing to drift from.
"""

from __future__ import annotations
