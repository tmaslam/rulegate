"""Eval harness tests, with emphasis on the honesty guarantees.

The tests below are the enforcement mechanism for the project's central rule:
a number that was not measured must never appear. If someone later "helpfully"
makes a metric default to 0.0, these fail.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.harness import (
    NOT_RUN,
    CalibrationReport,
    CaseResult,
    Comparison,
    Contains,
    EvalReport,
    EvalRunner,
    ExactMatch,
    GoldenCase,
    GoldenDataset,
    JsonSchemaMatch,
    JudgeCalibrator,
    MaxLatency,
    NotContains,
    NumericClose,
    RegexMatch,
    RunMetadata,
    RunStatus,
    ScaffoldResult,
    wilson_interval,
)

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


class TestAssertions:
    def test_exact_match(self):
        assert ExactMatch(value="ACK").evaluate("ACK", latency_ms=1).passed
        assert ExactMatch(value="ACK").evaluate(" ACK ", latency_ms=1).passed  # strip
        assert not ExactMatch(value="ACK").evaluate("ack", latency_ms=1).passed
        assert ExactMatch(value="ACK", case_sensitive=False).evaluate("ack", latency_ms=1).passed

    def test_failure_detail_is_actionable(self):
        outcome = ExactMatch(value="ACK").evaluate("NAK", latency_ms=1)
        assert not outcome.passed
        assert "ACK" in outcome.detail and "NAK" in outcome.detail

    def test_contains(self):
        assert Contains(value="Paris").evaluate("The capital is Paris.", latency_ms=1).passed
        assert not Contains(value="Berlin").evaluate("The capital is Paris.", latency_ms=1).passed

    def test_not_contains(self):
        assert NotContains(value="gsk_").evaluate("safe output", latency_ms=1).passed
        assert not NotContains(value="gsk_").evaluate("key gsk_123", latency_ms=1).passed

    def test_regex(self):
        assert RegexMatch(pattern=r"\d{4}-\d{2}-\d{2}").evaluate("2026-01-01", latency_ms=1).passed
        assert not RegexMatch(pattern=r"\d{4}").evaluate("no digits", latency_ms=1).passed

    def test_json_schema(self):
        good = JsonSchemaMatch(required_keys=("a",)).evaluate('{"a": 1}', latency_ms=1)
        assert good.passed
        missing = JsonSchemaMatch(required_keys=("b",)).evaluate('{"a": 1}', latency_ms=1)
        assert not missing.passed
        assert "missing keys" in missing.detail
        invalid = JsonSchemaMatch().evaluate("not json", latency_ms=1)
        assert not invalid.passed
        assert "invalid JSON" in invalid.detail

    def test_json_schema_rejects_non_object(self):
        assert not JsonSchemaMatch().evaluate("[1,2]", latency_ms=1).passed

    def test_numeric_close(self):
        assert NumericClose(value=7).evaluate("There are 7 continents", latency_ms=1).passed
        assert NumericClose(value=1000).evaluate("1,000 items", latency_ms=1).passed  # commas
        assert not NumericClose(value=7).evaluate("no number", latency_ms=1).passed
        assert NumericClose(value=1.0, tolerance=0.2).evaluate("1.1", latency_ms=1).passed

    def test_max_latency(self):
        assert MaxLatency(max_ms=100).evaluate("x", latency_ms=50).passed
        assert not MaxLatency(max_ms=100).evaluate("x", latency_ms=150).passed


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class TestGoldenDataset:
    def _write(self, tmp_path: Path, lines: list[str]) -> Path:
        path = tmp_path / "golden.v1.jsonl"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_loads_and_hashes(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            [
                "# a comment",
                "",
                json.dumps({"id": "a", "input": "x", "assertions": [{"kind": "contains", "value": "y"}]}),
            ],
        )
        dataset = GoldenDataset.load(path)
        assert len(dataset.cases) == 1
        assert len(dataset.sha256) == 64

    def test_hash_changes_with_content(self, tmp_path: Path):
        first = GoldenDataset.load(self._write(tmp_path, [json.dumps({"id": "a", "input": "x"})]))
        second = GoldenDataset.load(self._write(tmp_path, [json.dumps({"id": "a", "input": "y"})]))
        # Content-hash versioning is what makes a score attributable to bytes.
        assert first.sha256 != second.sha256

    def test_missing_file_raises_rather_than_scoring_zero_of_zero(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            GoldenDataset.load(tmp_path / "nope.jsonl")

    def test_empty_dataset_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="no cases"):
            GoldenDataset.load(self._write(tmp_path, ["# only a comment"]))

    def test_duplicate_ids_raise(self, tmp_path: Path):
        with pytest.raises(ValueError, match="duplicate case ids"):
            GoldenDataset.load(
                self._write(
                    tmp_path,
                    [json.dumps({"id": "a", "input": "x"}), json.dumps({"id": "a", "input": "y"})],
                )
            )

    def test_malformed_line_reports_line_number(self, tmp_path: Path):
        with pytest.raises(ValueError, match=":2:"):
            GoldenDataset.load(
                self._write(tmp_path, [json.dumps({"id": "a", "input": "x"}), "{not json"])
            )

    def test_shipped_template_dataset_loads(self):
        # Guards the dataset that ships with the repo.
        dataset = GoldenDataset.load("evals/datasets/golden.v1.jsonl")
        assert dataset.cases
        for case in dataset.cases:
            assert case.assertions, f"case {case.id} has no assertions and can never pass"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestWilson:
    def test_perfect_score_does_not_claim_certainty(self):
        interval = wilson_interval(20, 20)
        assert interval is not None
        # The Wald interval would return [1.0, 1.0] here — claiming certainty
        # from 20 samples. That overstatement is the whole reason for Wilson.
        assert interval.low < 0.9
        assert interval.high == pytest.approx(1.0)

    def test_half_is_centred(self):
        interval = wilson_interval(50, 100)
        assert interval is not None
        assert (interval.low + interval.high) / 2 == pytest.approx(0.5, abs=0.01)

    def test_more_samples_narrow_the_interval(self):
        small = wilson_interval(5, 10)
        large = wilson_interval(500, 1000)
        assert small is not None and large is not None
        assert (large.high - large.low) < (small.high - small.low)

    def test_no_data_yields_no_interval(self):
        assert wilson_interval(0, 0) is None

    def test_zero_score_is_bounded_at_zero(self):
        interval = wilson_interval(0, 20)
        assert interval is not None
        assert interval.low == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Honesty guarantees
# ---------------------------------------------------------------------------


def _completed_report(passed: int, total: int) -> EvalReport:
    results = tuple(
        CaseResult(
            case_id=f"c{i}",
            passed=i < passed,
            output="o",
            latency_ms=float(10 + i),
            cost_usd=Decimal(0),
        )
        for i in range(total)
    )
    meta = RunMetadata(
        dataset_name="golden.v1",
        dataset_split="test",
        dataset_sha256="a" * 64,
        dataset_path="evals/datasets/golden.v1.jsonl",
        model="fake/deterministic-v1",
        provider="fake",
        temperature=0.0,
        seed=0,
        scaffold="unit",
        scaffold_only=True,
        started_at="2026-01-01T00:00:00+00:00",
    )
    return EvalReport(status=RunStatus.COMPLETED, metadata=meta, results=results)


class TestNotRunHonesty:
    def test_every_metric_is_none(self):
        report = EvalReport.not_run("nothing ran")
        assert report.score is None
        assert report.score_ci is None
        assert report.latency_p50_ms is None
        assert report.latency_p95_ms is None
        assert report.total_cost_usd is None
        assert report.cost_per_request_usd is None

    def test_renders_the_literal_string(self):
        markdown = EvalReport.not_run("nothing ran").render_markdown()
        assert NOT_RUN in markdown
        assert "nothing ran" in markdown

    def test_renders_no_fabricated_numbers(self):
        # The data row must not contain a single digit-bearing metric.
        row = EvalReport.not_run("x").render_markdown().splitlines()[2]
        assert row.count(NOT_RUN) >= 8
        assert "0.0" not in row

    def test_partially_priced_run_reports_no_total(self):
        # Summing only the priced cases would be an undercount presented as a
        # total. All-or-nothing is the honest choice.
        report = _completed_report(2, 2)
        mixed = report.model_copy(
            update={
                "results": (
                    report.results[0],
                    report.results[1].model_copy(update={"cost_usd": None}),
                )
            }
        )
        assert mixed.total_cost_usd is None
        assert NOT_RUN in mixed.render_markdown()

    def test_scaffold_only_run_is_labelled(self):
        markdown = _completed_report(2, 2).render_markdown()
        assert "Scaffold-only run" in markdown
        assert "not** evidence about model quality" in markdown

    def test_completed_report_exposes_provenance(self):
        markdown = _completed_report(8, 10).render_markdown()
        assert "sha256:" in markdown
        assert "fake/deterministic-v1" in markdown

    def test_score_carries_a_confidence_interval(self):
        markdown = _completed_report(8, 10).render_markdown()
        assert "0.800 [" in markdown


class TestRunner:
    async def test_case_error_counts_as_failure_not_skip(self):
        # Dropping an erroring case would inflate the score of the survivors.
        dataset = GoldenDataset(
            name="d",
            split="test",
            sha256="a" * 64,
            path="d.jsonl",
            cases=(
                GoldenCase(id="ok", input="x", assertions=(Contains(value="y"),)),
                GoldenCase(id="boom", input="x", assertions=(Contains(value="y"),)),
            ),
        )

        async def scaffold(case: GoldenCase) -> ScaffoldResult:
            if case.id == "boom":
                msg = "scaffold exploded"
                raise RuntimeError(msg)
            return ScaffoldResult(output="y", latency_ms=1.0, cost_usd=Decimal(0))

        runner = EvalRunner(
            dataset=dataset,
            scaffold=scaffold,
            scaffold_name="unit",
            model="fake/v1",
            provider="fake",
            temperature=0.0,
            seed=0,
            scaffold_only=True,
        )
        report = await runner.run()
        assert report.total_cases == 2
        assert report.passed_cases == 1
        assert report.score == 0.5
        assert report.failures[0].error is not None

    async def test_case_without_assertions_cannot_pass(self):
        # A case that asserts nothing proves nothing; counting it as a pass
        # would silently inflate every score.
        dataset = GoldenDataset(
            name="d",
            split="test",
            sha256="a" * 64,
            path="d.jsonl",
            cases=(GoldenCase(id="empty", input="x", assertions=()),),
        )

        async def scaffold(case: GoldenCase) -> ScaffoldResult:
            return ScaffoldResult(output="anything", latency_ms=1.0)

        runner = EvalRunner(
            dataset=dataset,
            scaffold=scaffold,
            scaffold_name="unit",
            model="fake/v1",
            provider="fake",
            temperature=0.0,
            seed=0,
            scaffold_only=True,
        )
        report = await runner.run()
        assert report.score == 0.0


# ---------------------------------------------------------------------------
# Judge calibration
# ---------------------------------------------------------------------------


class TestJudgeCalibration:
    def test_confusion_matrix(self):
        report = JudgeCalibrator.compute(
            human_labels={"a": True, "b": True, "c": False, "d": False},
            judge_labels={"a": True, "b": False, "c": True, "d": False},
            judge_model="fake/judge-v1",
        )
        assert (report.true_positives, report.false_negatives) == (1, 1)
        assert (report.false_positives, report.true_negatives) == (1, 1)
        assert report.precision == pytest.approx(0.5)
        assert report.recall == pytest.approx(0.5)
        assert report.f1 == pytest.approx(0.5)

    def test_kappa_exposes_a_degenerate_always_pass_judge(self):
        # 90% of answers are good; the judge says "pass" unconditionally.
        # Accuracy flatters it at 0.90. Kappa correctly reports 0.
        report = JudgeCalibrator.compute(
            human_labels={f"c{i}": i < 9 for i in range(10)},
            judge_labels={f"c{i}": True for i in range(10)},
        )
        assert report.accuracy == pytest.approx(0.9)
        assert report.cohens_kappa == pytest.approx(0.0)

    def test_perfect_judge(self):
        report = JudgeCalibrator.compute(
            human_labels={"a": True, "b": False},
            judge_labels={"a": True, "b": False},
        )
        assert report.precision == pytest.approx(1.0)
        assert report.cohens_kappa == pytest.approx(1.0)

    def test_no_human_labels_is_not_run(self):
        report = JudgeCalibrator.compute(human_labels={}, judge_labels={"a": True})
        assert report.status is RunStatus.NOT_RUN
        assert report.precision is None
        assert NOT_RUN in report.render_markdown()

    def test_only_overlapping_ids_are_scored(self):
        report = JudgeCalibrator.compute(
            human_labels={"a": True, "unlabelled_by_judge": True},
            judge_labels={"a": True, "unlabelled_by_human": False},
        )
        assert report.n == 1

    def test_not_run_renders_the_reason(self):
        assert "unknown quality" in CalibrationReport.not_run("no labels").render_markdown()

    def test_from_report_without_judge_labels_is_not_run(self):
        dataset = GoldenDataset(
            name="d",
            split="test",
            sha256="a" * 64,
            path="d.jsonl",
            cases=(GoldenCase(id="a", input="x", human_label=True),),
        )
        report = _completed_report(1, 1)
        calibration = JudgeCalibrator.from_report(report, dataset)
        assert calibration.status is RunStatus.NOT_RUN


# ---------------------------------------------------------------------------
# CI regression gate
# ---------------------------------------------------------------------------


class TestRegressionGate:
    def test_blocks_a_measured_regression(self):
        comparison = Comparison(
            base=_completed_report(10, 10), head=_completed_report(5, 10), max_drop=0.03
        )
        assert comparison.delta == pytest.approx(-0.5)
        assert comparison.regressed
        assert "BLOCKED" in comparison.render_markdown()

    def test_allows_a_drop_within_tolerance(self):
        comparison = Comparison(
            base=_completed_report(100, 100), head=_completed_report(98, 100), max_drop=0.03
        )
        assert not comparison.regressed

    def test_allows_an_improvement(self):
        comparison = Comparison(
            base=_completed_report(5, 10), head=_completed_report(10, 10), max_drop=0.03
        )
        assert not comparison.regressed

    def test_missing_base_does_not_block(self):
        # Normal on a first PR. Blocking on missing data trains people to bypass
        # the gate, which is worse than not having one.
        comparison = Comparison(
            base=EvalReport.not_run("first PR"), head=_completed_report(10, 10), max_drop=0.03
        )
        assert not comparison.regressed
        assert not comparison.comparable
        assert NOT_RUN in comparison.render_markdown()

    def test_different_dataset_hash_is_not_comparable(self):
        base = _completed_report(10, 10)
        head = _completed_report(5, 10)
        assert head.metadata is not None
        head = head.model_copy(
            update={"metadata": head.metadata.model_copy(update={"dataset_sha256": "b" * 64})}
        )
        comparison = Comparison(base=base, head=head, max_drop=0.03)
        # A score comparison across different datasets is meaningless, so the
        # gate must abstain rather than "detect" a fake regression.
        assert not comparison.comparable
        assert not comparison.regressed
        assert "not comparable" in comparison.render_markdown().lower()

    def test_roundtrips_through_json(self, tmp_path: Path):
        path = tmp_path / "r.json"
        path.write_text(_completed_report(8, 10).to_json(), encoding="utf-8")
        loaded = EvalReport.from_json_file(path)
        assert loaded.score == pytest.approx(0.8)
