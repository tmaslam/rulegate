"""Versioned golden-dataset eval harness with Level-1 assertions and judge calibration.

The honesty rule, enforced in code
----------------------------------
This is an Upwork portfolio. One fabricated number ends a freelance career, so
this module is built so that inventing one is *hard*:

* Every metric is ``float | None``. ``None`` renders as ``not yet run`` — the
  literal string. There is no default, no placeholder, no "illustrative" value.
* :meth:`EvalReport.not_run` is the only way to produce a report without running,
  and every metric on it is ``None``. You cannot accidentally get a number out of
  a run that did not happen.
* A run against the deterministic fake is stamped ``scaffold_only=True`` and its
  Markdown says so in the table. A fake-backed score measures parsing and routing
  logic — it is **not** evidence about a model, and the renderer will not let it
  be presented as one.
* Cost is ``None`` unless every case had known pricing. A partially-priced run
  reports no total rather than an undercount.
* Scores carry a **Wilson 95% confidence interval**, because "0.86" on 50 cases
  means 0.86 ± ~0.09 and reporting the point estimate alone overstates it.

Every reported number carries: dataset+sha, split, metric, model+version,
temperature, seed, scaffold, cost, p50/p95 latency, and 95% CI. That is the
contract in README.md, and :meth:`EvalReport.render_markdown` is what fills it.

CLI::

    uv run python -m evals.harness run --dataset evals/datasets/golden.v1.jsonl \\
        --out evals/runs/head.json
    uv run python -m evals.harness compare --base evals/runs/base.json \\
        --head evals/runs/head.json --max-drop 0.03 --markdown evals/runs/table.md

Copy to ``src/<package>/evals/harness.py`` (keep datasets at ``evals/datasets/``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import structlog
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "NOT_RUN",
    "Assertion",
    "AssertionOutcome",
    "CalibrationReport",
    "CaseResult",
    "ConfidenceInterval",
    "Contains",
    "EvalReport",
    "EvalRunner",
    "ExactMatch",
    "GoldenCase",
    "GoldenDataset",
    "JsonSchemaMatch",
    "JudgeCalibrator",
    "MaxLatency",
    "NotContains",
    "NumericClose",
    "RegexMatch",
    "RunMetadata",
    "RunStatus",
    "wilson_interval",
]

log: Final = structlog.get_logger(__name__)

#: The literal string used for any metric that has not been measured. Do not
#: replace it with a number unless a run actually produced that number.
NOT_RUN: Final = "not yet run"

_Z_95: Final = 1.959963984540054  # two-sided 95% normal quantile


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class ConfidenceInterval(BaseModel):
    """A two-sided confidence interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: float
    high: float
    level: float = 0.95

    def render(self, *, digits: int = 3) -> str:
        """Render as ``[low, high]``."""
        return f"[{self.low:.{digits}f}, {self.high:.{digits}f}]"


def wilson_interval(successes: int, total: int, *, z: float = _Z_95) -> ConfidenceInterval | None:
    """Wilson score interval for a binomial proportion.

    Wilson rather than normal-approximation ("Wald") because Wald is badly wrong
    exactly where eval suites live: small n, and proportions near 0 or 1. Wald on
    20/20 gives [1.0, 1.0], claiming perfection with certainty. Wilson gives
    roughly [0.84, 1.0], which is the honest statement.

    Returns:
        The interval, or ``None`` when ``total`` is 0 — no data, no interval.
    """
    if total <= 0:
        return None
    p_hat = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p_hat + z2 / (2 * total)) / denominator
    margin = z / denominator * math.sqrt(p_hat * (1 - p_hat) / total + z2 / (4 * total * total))
    return ConfidenceInterval(
        low=max(0.0, center - margin),
        high=min(1.0, center + margin),
    )


def _percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile. ``None`` for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    return ordered[max(0, min(rank, len(ordered)) - 1)]


# ---------------------------------------------------------------------------
# Level-1 assertions — deterministic, no LLM, no network.
# ---------------------------------------------------------------------------


class AssertionOutcome(BaseModel):
    """Result of one assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    passed: bool
    #: Why it failed. Empty on pass. This is what a developer reads first.
    detail: str = ""


class _AssertionBase(BaseModel):
    """Shared config for Level-1 assertions.

    Deliberately declares no ``evaluate`` here. Every concrete assertion defines
    it, and callers only ever hold the :data:`Assertion` union — which mypy
    resolves attribute access across — so a base method would be unreachable
    code whose only job is to raise. Assertions are frozen and reject unknown
    fields so a typo in the golden JSONL fails at load, not silently at compare
    time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExactMatch(_AssertionBase):
    """Output must equal ``value`` exactly (after optional case folding/strip)."""

    kind: Literal["exact_match"] = "exact_match"
    value: str
    case_sensitive: bool = True
    strip: bool = True

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Compare output to the expected value."""
        actual = output.strip() if self.strip else output
        expected = self.value.strip() if self.strip else self.value
        if not self.case_sensitive:
            actual, expected = actual.lower(), expected.lower()
        passed = actual == expected
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"expected {expected!r}, got {actual!r}",
        )


class Contains(_AssertionBase):
    """Output must contain ``value``."""

    kind: Literal["contains"] = "contains"
    value: str
    case_sensitive: bool = False

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Check for the substring."""
        haystack = output if self.case_sensitive else output.lower()
        needle = self.value if self.case_sensitive else self.value.lower()
        passed = needle in haystack
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"{self.value!r} not found in output",
        )


class NotContains(_AssertionBase):
    """Output must NOT contain ``value``. Useful for leakage and refusal checks."""

    kind: Literal["not_contains"] = "not_contains"
    value: str
    case_sensitive: bool = False

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Check the substring is absent."""
        haystack = output if self.case_sensitive else output.lower()
        needle = self.value if self.case_sensitive else self.value.lower()
        passed = needle not in haystack
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"forbidden string {self.value!r} present in output",
        )


class RegexMatch(_AssertionBase):
    """Output must match ``pattern``."""

    kind: Literal["regex"] = "regex"
    pattern: str

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Apply the regex."""
        passed = re.search(self.pattern, output) is not None
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"pattern {self.pattern!r} did not match",
        )


class JsonSchemaMatch(_AssertionBase):
    """Output must be JSON with the required keys.

    Structural only — deliberately not a full JSON Schema validator, which would
    add a dependency for little gain. Strong typing at the boundary is the
    gateway's job (``acomplete_model``); this catches shape regressions in evals.
    """

    kind: Literal["json_schema"] = "json_schema"
    required_keys: tuple[str, ...] = ()

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Parse and check required keys."""
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return AssertionOutcome(kind=self.kind, passed=False, detail=f"invalid JSON: {exc}")
        if not isinstance(parsed, dict):
            return AssertionOutcome(
                kind=self.kind, passed=False, detail=f"expected object, got {type(parsed).__name__}"
            )
        missing = [k for k in self.required_keys if k not in parsed]
        return AssertionOutcome(
            kind=self.kind,
            passed=not missing,
            detail="" if not missing else f"missing keys: {missing}",
        )


class NumericClose(_AssertionBase):
    """The first number in the output must be within ``tolerance`` of ``value``."""

    kind: Literal["numeric_close"] = "numeric_close"
    value: float
    tolerance: float = 1e-6

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Extract the leading number and compare within tolerance."""
        match = re.search(r"-?\d+(?:\.\d+)?", output.replace(",", ""))
        if match is None:
            return AssertionOutcome(kind=self.kind, passed=False, detail="no number in output")
        actual = float(match.group())
        passed = abs(actual - self.value) <= self.tolerance
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"expected {self.value} ±{self.tolerance}, got {actual}",
        )


class MaxLatency(_AssertionBase):
    """Measured latency must not exceed ``max_ms``.

    Only meaningful on a live run. Against the fake, latency is simulated — a pass
    here proves nothing about production and the report marks the run
    scaffold-only for exactly that reason.
    """

    kind: Literal["max_latency_ms"] = "max_latency_ms"
    max_ms: float

    def evaluate(self, output: str, *, latency_ms: float) -> AssertionOutcome:  # noqa: ARG002
        """Compare measured latency against the budget."""
        passed = latency_ms <= self.max_ms
        return AssertionOutcome(
            kind=self.kind,
            passed=passed,
            detail="" if passed else f"{latency_ms:.1f}ms exceeded budget {self.max_ms}ms",
        )


#: Discriminated union of every Level-1 assertion, keyed on `kind`. PEP 695
#: syntax; verified working as a pydantic field type on GoldenCase, which is how
#: assertions are parsed out of the golden JSONL.
type Assertion = Annotated[
    ExactMatch | Contains | NotContains | RegexMatch | JsonSchemaMatch | NumericClose | MaxLatency,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class GoldenCase(BaseModel):
    """One case in the golden dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    input: str
    #: Free-form reference answer. Documentation for humans; assertions decide.
    expected: str | None = None
    assertions: tuple[Assertion, ...] = ()
    tags: tuple[str, ...] = ()
    #: Ground-truth label from a HUMAN, for judge calibration. Never machine-set:
    #: labelling with a model and calibrating against it measures nothing.
    human_label: bool | None = None
    #: System prompt override for this case.
    system: str | None = None


class GoldenDataset(BaseModel):
    """A versioned golden dataset, loaded from JSONL in git.

    Versioning is by **content hash**, not a hand-maintained version string. A
    number is only attributable to the exact bytes that produced it; a string
    someone forgot to bump is worse than no version at all. The hash goes into
    every report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    split: str
    cases: tuple[GoldenCase, ...]
    #: SHA-256 of the raw file bytes.
    sha256: str
    path: str

    @classmethod
    def load(cls, path: Path | str, *, split: str = "test") -> Self:
        """Load a JSONL dataset.

        One JSON object per line. Blank lines and ``#`` comment lines are skipped.
        A sibling ``<stem>.meta.json`` may supply ``name``/``split``.

        Raises:
            FileNotFoundError: The dataset is missing. Never silently empty — an
                empty dataset would score 0/0 and render a misleading table.
            ValueError: A line is malformed, or ids are not unique.
        """
        dataset_path = Path(path)
        if not dataset_path.is_file():
            msg = f"golden dataset not found: {dataset_path}"
            raise FileNotFoundError(msg)

        raw = dataset_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()

        cases: list[GoldenCase] = []
        for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                cases.append(GoldenCase.model_validate_json(stripped))
            except Exception as exc:
                msg = f"{dataset_path}:{lineno}: invalid case: {exc}"
                raise ValueError(msg) from exc

        if not cases:
            msg = f"{dataset_path} contains no cases"
            raise ValueError(msg)

        ids = [c.id for c in cases]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            msg = f"{dataset_path}: duplicate case ids: {sorted(duplicates)}"
            raise ValueError(msg)

        name = dataset_path.stem
        resolved_split = split
        meta_path = dataset_path.with_suffix(".meta.json")
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            name = str(meta.get("name", name))
            resolved_split = str(meta.get("split", resolved_split))

        return cls(
            name=name,
            split=resolved_split,
            cases=tuple(cases),
            sha256=digest,
            path=dataset_path.as_posix(),
        )

    def filter_by_tag(self, tag: str) -> tuple[GoldenCase, ...]:
        """Cases carrying ``tag``."""
        return tuple(c for c in self.cases if tag in c.tags)


# ---------------------------------------------------------------------------
# Run results
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    """Whether a run happened."""

    NOT_RUN = "not_run"
    COMPLETED = "completed"


class CaseResult(BaseModel):
    """Outcome for a single case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    passed: bool
    output: str
    outcomes: tuple[AssertionOutcome, ...] = ()
    latency_ms: float
    #: None when pricing is unknown. Never guessed.
    cost_usd: Decimal | None = None
    provider: str | None = None
    model: str | None = None
    #: Set when the case raised instead of returning.
    error: str | None = None
    #: The judge's verdict, when an LLM judge ran. Independent of `passed`.
    judge_label: bool | None = None


class RunMetadata(BaseModel):
    """Everything needed to reproduce and correctly caption a number.

    Every field here appears in the README eval table. A number without this
    metadata is not reportable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_name: str
    dataset_split: str
    dataset_sha256: str
    dataset_path: str
    #: e.g. "groq/llama-3.3-70b-versatile" — the RESOLVED model, not an alias.
    model: str
    provider: str
    temperature: float
    seed: int | None
    #: Short description of the scaffold under test, e.g. "single-shot",
    #: "react-agent-v2", "rag-top5-rerank". A score is meaningless without it.
    scaffold: str
    #: True when the run used the deterministic fake. Such a run measures the
    #: scaffold ONLY and is not evidence about any model.
    scaffold_only: bool
    started_at: str
    git_sha: str | None = None
    harness_version: str = "1.0.0"


class EvalReport(BaseModel):
    """A complete, self-describing eval report.

    ``status=NOT_RUN`` ⇒ every metric property returns ``None`` ⇒ every cell
    renders ``not yet run``. That is the whole point of this class.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RunStatus
    metadata: RunMetadata | None = None
    results: tuple[CaseResult, ...] = ()
    #: Why the run did not happen, when status is NOT_RUN.
    not_run_reason: str | None = None

    @classmethod
    def not_run(cls, reason: str) -> Self:
        """Build a report for a run that did not happen.

        The only supported way to produce a report without running. Every metric
        is ``None``; nothing can render as a number.
        """
        return cls(status=RunStatus.NOT_RUN, not_run_reason=reason)

    # -- metrics: all `| None`, all `None` when the run did not happen --------

    @property
    def total_cases(self) -> int:
        """Number of cases executed."""
        return len(self.results)

    @property
    def passed_cases(self) -> int:
        """Number of cases that passed every assertion."""
        return sum(1 for r in self.results if r.passed)

    @property
    def score(self) -> float | None:
        """Pass rate in [0, 1], or None when nothing ran."""
        if self.status is RunStatus.NOT_RUN or not self.results:
            return None
        return self.passed_cases / self.total_cases

    @property
    def score_ci(self) -> ConfidenceInterval | None:
        """Wilson 95% CI on the pass rate, or None when nothing ran."""
        if self.status is RunStatus.NOT_RUN or not self.results:
            return None
        return wilson_interval(self.passed_cases, self.total_cases)

    @property
    def latency_p50_ms(self) -> float | None:
        """Median latency, or None when nothing ran."""
        if self.status is RunStatus.NOT_RUN:
            return None
        return _percentile([r.latency_ms for r in self.results], 50)

    @property
    def latency_p95_ms(self) -> float | None:
        """95th-percentile latency, or None when nothing ran."""
        if self.status is RunStatus.NOT_RUN:
            return None
        return _percentile([r.latency_ms for r in self.results], 95)

    @property
    def total_cost_usd(self) -> Decimal | None:
        """Total cost, or None if ANY case had unknown pricing.

        All-or-nothing on purpose: summing only the priced cases would silently
        under-report, and an undercount presented as a total is a fabrication.
        """
        if self.status is RunStatus.NOT_RUN or not self.results:
            return None
        if any(r.cost_usd is None for r in self.results):
            return None
        return sum((r.cost_usd for r in self.results if r.cost_usd is not None), Decimal(0))

    @property
    def cost_per_request_usd(self) -> Decimal | None:
        """Mean cost per request, or None when the total is unknown."""
        total = self.total_cost_usd
        if total is None or not self.results:
            return None
        return total / Decimal(len(self.results))

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        """Cases that failed. Feeds the README's "How it fails" section."""
        return tuple(r for r in self.results if not r.passed)

    # -- rendering ------------------------------------------------------------

    @staticmethod
    def _cell(value: object, *, fmt: str = "") -> str:
        """Render a metric cell. ``None`` becomes the literal ``not yet run``."""
        if value is None:
            return NOT_RUN
        if isinstance(value, ConfidenceInterval):
            return value.render()
        if fmt and isinstance(value, int | float | Decimal):
            return format(value, fmt)
        return str(value)

    def render_markdown(self) -> str:
        """Render the README eval table row set.

        Columns match README.md.tmpl exactly. Unmeasured cells say ``not yet run``.
        """
        meta = self.metadata
        rows = [
            "| Dataset | Split | Metric | Model + version | Temp | Seed | Scaffold "
            "| Score (95% CI) | p50 / p95 latency | Cost/req |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        score = self._cell(self.score, fmt=".3f")
        ci = self._cell(self.score_ci)
        score_cell = NOT_RUN if self.score is None else f"{score} {ci}"
        p50 = self._cell(self.latency_p50_ms, fmt=".0f")
        p95 = self._cell(self.latency_p95_ms, fmt=".0f")
        latency_cell = (
            NOT_RUN
            if self.latency_p50_ms is None or self.latency_p95_ms is None
            else f"{p50} ms / {p95} ms"
        )
        cost = self.cost_per_request_usd
        cost_cell = NOT_RUN if cost is None else f"${cost:.6f}"

        rows.append(
            "| {dataset} | {split} | {metric} | {model} | {temp} | {seed} | {scaffold} "
            "| {score} | {latency} | {cost} |".format(
                dataset=self._cell(meta.dataset_name if meta else None),
                split=self._cell(meta.dataset_split if meta else None),
                metric="pass rate (Level-1 assertions)",
                model=self._cell(meta.model if meta else None),
                temp=self._cell(meta.temperature if meta else None),
                seed=self._cell(meta.seed if meta else None),
                scaffold=self._cell(meta.scaffold if meta else None),
                score=score_cell,
                latency=latency_cell,
                cost=cost_cell,
            )
        )

        if self.status is RunStatus.NOT_RUN:
            rows.append("")
            rows.append(f"> Not yet run: {self.not_run_reason or 'no run recorded'}.")
        elif meta is not None and meta.scaffold_only:
            rows.append("")
            rows.append(
                "> **Scaffold-only run.** Executed against the deterministic fake "
                "provider (no network, no API key). This measures routing, parsing "
                "and assertion logic — it is **not** evidence about model quality. "
                "The latency and cost columns describe the fake (in-process, $0), "
                "not any real provider, and must not be quoted as performance "
                "figures."
            )
        if meta is not None:
            rows.append("")
            rows.append(
                f"> Dataset `{meta.dataset_path}` @ `sha256:{meta.dataset_sha256[:12]}`, "
                f"run at {meta.started_at}"
                + (f", commit `{meta.git_sha[:8]}`" if meta.git_sha else "")
                + "."
            )
        return "\n".join(rows)

    def to_json(self) -> str:
        """Serialise for CI artifacts and base-vs-head comparison."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json_file(cls, path: Path | str) -> Self:
        """Load a report written by :meth:`to_json`."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ScaffoldResult(BaseModel):
    """What a scaffold returns for one case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: str
    latency_ms: float
    cost_usd: Decimal | None = None
    provider: str | None = None
    model: str | None = None


#: A scaffold under test: takes a case and returns its output plus the measured
#: latency/cost. Keeping this a plain callable means the harness has no opinion
#: about your agent's internals — wire any graph, chain or agent loop to it.
type ScaffoldFn = Callable[[GoldenCase], Awaitable[ScaffoldResult]]


def _git_sha() -> str | None:
    """Current commit, or None outside a git checkout. Never fabricated."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 — `git` from PATH is intended.
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


class EvalRunner:
    """Runs a scaffold over a golden dataset and produces an :class:`EvalReport`.

    Concurrency defaults to 4 — free tiers are rate-limited, and a runner that
    stampedes them just converts quota into backoff.
    """

    def __init__(
        self,
        *,
        dataset: GoldenDataset,
        scaffold: ScaffoldFn,
        scaffold_name: str,
        model: str,
        provider: str,
        temperature: float,
        seed: int | None,
        scaffold_only: bool,
        concurrency: int = 4,
    ) -> None:
        self._dataset = dataset
        self._scaffold = scaffold
        self._scaffold_name = scaffold_name
        self._model = model
        self._provider = provider
        self._temperature = temperature
        self._seed = seed
        self._scaffold_only = scaffold_only
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _run_case(self, case: GoldenCase) -> CaseResult:
        async with self._semaphore:
            try:
                result = await self._scaffold(case)
            except Exception as exc:  # noqa: BLE001 — see below; must not escape.
                # An erroring case is a FAILING case, never a skipped one.
                # Dropping it would inflate the score of the survivors, which is
                # precisely the kind of quiet number inflation this repo exists
                # to avoid. A scaffold is arbitrary user code and may raise
                # anything, so the catch is deliberately broad.
                log.warning("eval_case_error", case_id=case.id, error=str(exc))
                return CaseResult(
                    case_id=case.id,
                    passed=False,
                    output="",
                    latency_ms=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )

        outcomes = tuple(
            assertion.evaluate(result.output, latency_ms=result.latency_ms)
            for assertion in case.assertions
        )
        # No assertions ⇒ the case cannot pass. A case that asserts nothing
        # proves nothing, and counting it as a pass would inflate the score.
        passed = bool(outcomes) and all(o.passed for o in outcomes)
        return CaseResult(
            case_id=case.id,
            passed=passed,
            output=result.output,
            outcomes=outcomes,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            provider=result.provider,
            model=result.model,
        )

    async def run(self) -> EvalReport:
        """Execute every case and build the report."""
        started = datetime.now(tz=UTC).isoformat(timespec="seconds")
        results = await asyncio.gather(*(self._run_case(c) for c in self._dataset.cases))
        metadata = RunMetadata(
            dataset_name=self._dataset.name,
            dataset_split=self._dataset.split,
            dataset_sha256=self._dataset.sha256,
            dataset_path=self._dataset.path,
            model=self._model,
            provider=self._provider,
            temperature=self._temperature,
            seed=self._seed,
            scaffold=self._scaffold_name,
            scaffold_only=self._scaffold_only,
            started_at=started,
            git_sha=_git_sha(),
        )
        report = EvalReport(
            status=RunStatus.COMPLETED,
            metadata=metadata,
            results=tuple(results),
        )
        log.info(
            "eval_complete",
            dataset=self._dataset.name,
            passed=report.passed_cases,
            total=report.total_cases,
            scaffold_only=self._scaffold_only,
        )
        return report


# ---------------------------------------------------------------------------
# Judge calibration
# ---------------------------------------------------------------------------


class CalibrationReport(BaseModel):
    """Precision/recall of an LLM judge measured against human labels.

    Why this exists
    ---------------
    An LLM judge is a *measuring instrument*, and an uncalibrated instrument
    produces numbers of unknown worth. Reporting "the judge says 87%" without
    reporting the judge's own precision/recall against human labels is reporting
    a reading from a scale nobody ever checked.

    ``status=NOT_RUN`` when no human labels exist. All metrics are then ``None``
    and render ``not yet run``. Labelling with a model and calibrating against
    those labels measures the judge against itself and is not supported here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RunStatus
    #: Judge said pass, human said pass.
    true_positives: int = 0
    #: Judge said pass, human said fail. These are the dangerous ones — the judge
    #: waving through a bad answer.
    false_positives: int = 0
    true_negatives: int = 0
    #: Judge said fail, human said pass.
    false_negatives: int = 0
    judge_model: str | None = None
    not_run_reason: str | None = None

    @classmethod
    def not_run(cls, reason: str) -> Self:
        """Build a calibration report for a calibration that did not happen."""
        return cls(status=RunStatus.NOT_RUN, not_run_reason=reason)

    @property
    def n(self) -> int:
        """Number of co-labelled items."""
        return (
            self.true_positives + self.false_positives + self.true_negatives + self.false_negatives
        )

    @property
    def precision(self) -> float | None:
        """Of the items the judge passed, the fraction humans also passed."""
        if self.status is RunStatus.NOT_RUN:
            return None
        denominator = self.true_positives + self.false_positives
        if denominator == 0:
            return None
        return self.true_positives / denominator

    @property
    def precision_ci(self) -> ConfidenceInterval | None:
        """Wilson 95% CI on precision."""
        if self.status is RunStatus.NOT_RUN:
            return None
        return wilson_interval(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float | None:
        """Of the items humans passed, the fraction the judge also passed."""
        if self.status is RunStatus.NOT_RUN:
            return None
        denominator = self.true_positives + self.false_negatives
        if denominator == 0:
            return None
        return self.true_positives / denominator

    @property
    def recall_ci(self) -> ConfidenceInterval | None:
        """Wilson 95% CI on recall."""
        if self.status is RunStatus.NOT_RUN:
            return None
        return wilson_interval(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float | None:
        """Harmonic mean of precision and recall."""
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or (precision + recall) == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    @property
    def accuracy(self) -> float | None:
        """Raw agreement rate. Reported alongside kappa, never instead of it."""
        if self.status is RunStatus.NOT_RUN or self.n == 0:
            return None
        return (self.true_positives + self.true_negatives) / self.n

    @property
    def cohens_kappa(self) -> float | None:
        """Chance-corrected agreement.

        Reported because raw accuracy flatters a judge on an unbalanced set: if
        90% of answers are good, a judge that says "pass" unconditionally scores
        0.90 accuracy and κ = 0. Kappa exposes that; accuracy hides it.
        """
        if self.status is RunStatus.NOT_RUN or self.n == 0:
            return None
        n = self.n
        observed = (self.true_positives + self.true_negatives) / n
        judge_pass = (self.true_positives + self.false_positives) / n
        human_pass = (self.true_positives + self.false_negatives) / n
        expected = judge_pass * human_pass + (1 - judge_pass) * (1 - human_pass)
        if math.isclose(expected, 1.0):
            return None  # Undefined: no room for chance-corrected agreement.
        return (observed - expected) / (1 - expected)

    def render_markdown(self) -> str:
        """Render the calibration table. Unmeasured cells say ``not yet run``."""

        def cell(value: float | None, *, digits: int = 3) -> str:
            return NOT_RUN if value is None else f"{value:.{digits}f}"

        def with_ci(value: float | None, interval: ConfidenceInterval | None) -> str:
            if value is None:
                return NOT_RUN
            return f"{value:.3f}" + (f" {interval.render()}" if interval else "")

        rows = [
            "| Judge model | n (human-labelled) | Precision (95% CI) | Recall (95% CI) "
            "| F1 | Accuracy | Cohen's κ |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            f"| {self.judge_model or NOT_RUN} "
            f"| {self.n if self.status is RunStatus.COMPLETED else NOT_RUN} "
            f"| {with_ci(self.precision, self.precision_ci)} "
            f"| {with_ci(self.recall, self.recall_ci)} "
            f"| {cell(self.f1)} | {cell(self.accuracy)} | {cell(self.cohens_kappa)} |",
        ]
        if self.status is RunStatus.NOT_RUN:
            rows.append("")
            rows.append(
                f"> Not yet run: {self.not_run_reason or 'no human labels available'}. "
                "An LLM judge without calibration against human labels produces "
                "numbers of unknown quality; none are reported here."
            )
        return "\n".join(rows)


class JudgeCalibrator:
    """Computes :class:`CalibrationReport` from human vs. judge labels."""

    @staticmethod
    def compute(
        *,
        human_labels: Mapping[str, bool],
        judge_labels: Mapping[str, bool],
        judge_model: str | None = None,
    ) -> CalibrationReport:
        """Compare judge labels against human labels on their shared ids.

        Only ids present in BOTH maps are scored — an id the human never labelled
        has no ground truth, and guessing one would defeat the purpose.

        Args:
            human_labels: case id -> ground truth. Must come from a human.
            judge_labels: case id -> the LLM judge's verdict.
            judge_model: Judge model+version, recorded in the report.

        Returns:
            A completed report, or a ``not_run`` report when there is no overlap.
        """
        shared = sorted(set(human_labels) & set(judge_labels))
        if not shared:
            return CalibrationReport.not_run("no cases carry both a human label and a judge label")

        tp = fp = tn = fn = 0
        for case_id in shared:
            human, judge = human_labels[case_id], judge_labels[case_id]
            if judge and human:
                tp += 1
            elif judge and not human:
                fp += 1
            elif not judge and human:
                fn += 1
            else:
                tn += 1

        report = CalibrationReport(
            status=RunStatus.COMPLETED,
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
            judge_model=judge_model,
        )
        log.info(
            "judge_calibrated",
            n=report.n,
            precision=report.precision,
            recall=report.recall,
            kappa=report.cohens_kappa,
        )
        return report

    @staticmethod
    def from_report(
        report: EvalReport,
        dataset: GoldenDataset,
        *,
        judge_model: str | None = None,
    ) -> CalibrationReport:
        """Calibrate using ``human_label`` on the dataset and ``judge_label`` on the run."""
        human: dict[str, bool] = {
            c.id: c.human_label for c in dataset.cases if c.human_label is not None
        }
        judge: dict[str, bool] = {
            r.case_id: r.judge_label for r in report.results if r.judge_label is not None
        }
        if not human:
            return CalibrationReport.not_run(
                f"dataset {dataset.name!r} carries no human_label fields"
            )
        if not judge:
            return CalibrationReport.not_run("no judge labels recorded in this run")
        return JudgeCalibrator.compute(
            human_labels=human,
            judge_labels=judge,
            judge_model=judge_model,
        )


# ---------------------------------------------------------------------------
# Base-vs-head comparison (CI merge gate)
# ---------------------------------------------------------------------------


class Comparison(BaseModel):
    """Base-vs-head eval comparison for the CI merge gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: EvalReport
    head: EvalReport
    #: Maximum tolerated ABSOLUTE score drop, e.g. 0.03 = 3 percentage points.
    max_drop: float

    @property
    def delta(self) -> float | None:
        """head - base. None when either side did not run."""
        if self.base.score is None or self.head.score is None:
            return None
        return self.head.score - self.base.score

    @property
    def comparable(self) -> bool:
        """Whether the two runs are measuring the same thing.

        A score comparison across different datasets is meaningless, so the gate
        refuses to draw a conclusion when the dataset content hash differs.
        """
        if self.base.metadata is None or self.head.metadata is None:
            return False
        return self.base.metadata.dataset_sha256 == self.head.metadata.dataset_sha256

    @property
    def regressed(self) -> bool:
        """Whether the gate should block the merge.

        Blocks only on a *measured* regression. An unrunnable or incomparable
        base does not block — it warns. Blocking on missing data trains people to
        bypass the gate, which is worse than not having one.
        """
        delta = self.delta
        if delta is None or not self.comparable:
            return False
        return delta < -self.max_drop

    def render_markdown(self) -> str:
        """Render the PR comment table."""

        def score_cell(report: EvalReport) -> str:
            if report.score is None:
                return NOT_RUN
            ci = report.score_ci
            return f"{report.score:.3f}" + (f" {ci.render()}" if ci else "")

        delta = self.delta
        delta_cell = NOT_RUN if delta is None else f"{delta:+.3f}"
        verdict = (
            "**BLOCKED** - score regression exceeds the gate"
            if self.regressed
            else "PASS - within tolerance"
        )
        if delta is None or not self.comparable:
            verdict = "NOT COMPARABLE - gate not applied"

        lines = [
            "### Eval comparison",
            "",
            "| | Base | Head | Δ |",
            "| --- | --- | --- | --- |",
            f"| Score (95% CI) | {score_cell(self.base)} | {score_cell(self.head)} "
            f"| {delta_cell} |",
            f"| Cases passed | {self._cases(self.base)} | {self._cases(self.head)} | |",
            "",
            f"**Gate:** max absolute drop {self.max_drop:+.3f} — {verdict}",
            "",
        ]
        if not self.comparable:
            lines.append(
                "> **Warning:** base and head ran against different dataset content "
                "hashes (or one did not run). Scores across different datasets are "
                "not comparable, so no regression conclusion is drawn."
            )
            lines.append("")
        lines.append("<details><summary>Head run detail</summary>")
        lines.append("")
        lines.append(self.head.render_markdown())
        lines.append("")
        lines.append("</details>")
        return "\n".join(lines)

    @staticmethod
    def _cases(report: EvalReport) -> str:
        if report.status is RunStatus.NOT_RUN:
            return NOT_RUN
        return f"{report.passed_cases}/{report.total_cases}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _run_with_fake(dataset_path: Path, split: str) -> EvalReport:
    """Run the dataset against the deterministic fake.

    This is what CI executes with no secrets configured. It exercises the whole
    harness — loading, assertions, scoring, CI computation, rendering — for free
    and offline, and stamps the report ``scaffold_only=True`` so the resulting
    number can never be mistaken for a model result.

    Each project SHOULD replace this with its real scaffold wired to the fake
    backend, so the eval exercises the actual agent graph.
    """
    # Lazy imports (PLC0415 waived): keeps the fake out of the library import
    # path, so importing the harness in production code never pulls in test
    # doubles.
    from policy_guarded_ops_agent.fakes.fake_llm import (  # noqa: PLC0415
        FAKE_PROVIDER_NAME,
        FakeLLMBackend,
        FakeRule,
        fake_provider_spec,
    )
    from policy_guarded_ops_agent.llm.gateway import (  # noqa: PLC0415
        ChatMessage,
        CompletionRequest,
        Gateway,
    )

    dataset = GoldenDataset.load(dataset_path, split=split)
    # Wire each case's expected answer into the fake so the scaffold path is
    # exercised end-to-end. This is a SCAFFOLD test: it proves the harness and
    # plumbing work, and deliberately proves nothing about a model.
    rules: list[FakeRule] = [
        FakeRule(contains=case.input, response=case.expected)
        for case in dataset.cases
        if case.expected is not None
    ]
    backend = FakeLLMBackend(rules=rules)
    spec = fake_provider_spec()
    gateway = Gateway(chain=[spec], backend=backend)

    async def scaffold(case: GoldenCase) -> ScaffoldResult:
        messages: list[ChatMessage] = []
        if case.system is not None:
            messages.append(ChatMessage(role="system", content=case.system))
        messages.append(ChatMessage(role="user", content=case.input))
        response = await gateway.acomplete(
            CompletionRequest(messages=tuple(messages), temperature=0.0, seed=0)
        )
        return ScaffoldResult(
            output=response.text,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
            provider=response.provider,
            model=response.model,
        )

    runner = EvalRunner(
        dataset=dataset,
        scaffold=scaffold,
        scaffold_name="fake-single-shot",
        model=spec.model,
        provider=FAKE_PROVIDER_NAME,
        temperature=0.0,
        seed=0,
        scaffold_only=True,
    )
    return await runner.run()


def _cmd_run(args: argparse.Namespace) -> int:
    report = asyncio.run(_run_with_fake(Path(args.dataset), args.split))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_json(), encoding="utf-8")
    sys.stdout.write(report.render_markdown() + "\n")
    sys.stdout.write(f"\nreport written to {out}\n")
    if args.min_score is not None:
        score = report.score
        if score is None:
            sys.stderr.write("ERROR: --min-score given but the run produced no score\n")
            return 1
        if score < args.min_score:
            sys.stderr.write(f"ERROR: score {score:.3f} below --min-score {args.min_score:.3f}\n")
            return 1
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    head = EvalReport.from_json_file(args.head)
    base = (
        EvalReport.from_json_file(args.base)
        if Path(args.base).is_file()
        # A missing base is normal on a first PR or a new dataset. Report it as
        # not-run and let the gate abstain rather than inventing a baseline.
        else EvalReport.not_run(f"no base report at {args.base}")
    )
    comparison = Comparison(base=base, head=head, max_drop=args.max_drop)
    markdown = comparison.render_markdown()
    sys.stdout.write(markdown + "\n")
    if args.markdown:
        path = Path(args.markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    if comparison.regressed:
        # `regressed` is only True when delta is a real measured number, but be
        # explicit rather than relying on that invariant holding through edits.
        delta = comparison.delta
        drop = f"{delta:.3f}" if delta is not None else NOT_RUN
        sys.stderr.write(
            f"ERROR: eval score regressed by {drop}, exceeding the {args.max_drop:.3f} gate\n"
        )
        return 1
    return 0


def _force_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to a legacy codepage (cp1252 here), which cannot
    encode the characters this harness emits — the Greek delta in the comparison
    table, the em-dash in report captions, the kappa in the calibration table.
    Without this, `python -m evals.harness compare` dies with UnicodeEncodeError
    on a developer's machine while passing fine in CI on Linux. Cheap to prevent;
    confusing to debug.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="evals.harness", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the golden dataset against the fake provider")
    run.add_argument("--dataset", default="evals/datasets/golden.v1.jsonl")
    run.add_argument("--split", default="test")
    run.add_argument("--out", default="evals/runs/head.json")
    run.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="fail if the score is below this. Omit to only record the score.",
    )
    run.set_defaults(func=_cmd_run)

    compare = sub.add_parser("compare", help="compare two reports and apply the merge gate")
    compare.add_argument("--base", required=True)
    compare.add_argument("--head", required=True)
    compare.add_argument(
        "--max-drop",
        type=float,
        default=0.03,
        help="maximum tolerated absolute score drop (default 0.03 = 3 points)",
    )
    compare.add_argument("--markdown", default=None, help="also write the table here")
    compare.set_defaults(func=_cmd_compare)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
