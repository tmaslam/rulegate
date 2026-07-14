"""Versioned golden-dataset eval harness, Level-1 assertions, judge calibration.

Deliberately free of re-exports. ``harness`` is executed as a CLI via
``python -m evals.harness``, and eagerly importing it here would put it in
``sys.modules`` before ``runpy`` executes it — which triggers::

    RuntimeWarning: 'evals.harness' found in sys.modules after import of package
    'evals', but prior to execution of 'evals.harness'

and, worse, gives the module two identities. Import from the module directly::

    from evals.harness import EvalReport, EvalRunner, GoldenDataset
"""

from __future__ import annotations
