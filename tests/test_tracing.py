"""Tracing tests — the zero-account guarantee is a claim, so it gets a test."""

from __future__ import annotations

from decimal import Decimal

import pytest

from policy_guarded_ops_agent.fakes.fake_llm import user_request
from policy_guarded_ops_agent.llm.gateway import CompletionResponse, Usage
from policy_guarded_ops_agent.obs.tracing import (
    GenAI,
    TracingConfig,
    configure_tracing,
    get_tracer,
    llm_span,
    record_retrieval,
    record_tool_call,
    shutdown_tracing,
)


class TestConfig:
    def test_disabled_without_keys(self) -> None:
        assert not TracingConfig(env={}).enabled

    def test_disabled_with_only_one_key(self) -> None:
        assert not TracingConfig(env={"LANGFUSE_PUBLIC_KEY": "pk"}).enabled

    def test_blank_keys_do_not_enable(self) -> None:
        assert not TracingConfig(env={"LANGFUSE_PUBLIC_KEY": " ", "LANGFUSE_SECRET_KEY": " "}).enabled

    def test_enabled_with_both_keys(self) -> None:
        assert TracingConfig(env={"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}).enabled

    def test_otlp_endpoint_is_langfuse_path(self) -> None:
        config = TracingConfig(
            env={"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
                 "LANGFUSE_HOST": "https://cloud.langfuse.com/"}
        )
        assert config.otlp_endpoint == "https://cloud.langfuse.com/api/public/otel/v1/traces"

    def test_auth_header_is_basic_base64(self) -> None:
        config = TracingConfig(env={"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})
        # base64("pk:sk") == "cGs6c2s="
        assert config.otlp_headers["Authorization"] == "Basic cGs6c2s="

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1.0", 1.0), ("0.5", 0.5), ("2.0", 1.0), ("-1", 0.0), ("garbage", 1.0)],
    )
    def test_sample_rate_is_clamped_and_never_crashes(self, raw: str, expected: float) -> None:
        # A malformed env var must not take the app down at startup.
        assert TracingConfig(env={"LANGFUSE_SAMPLE_RATE": raw}).sample_rate == expected

    def test_bodies_captured_locally_but_not_in_prod_by_default(self) -> None:
        assert TracingConfig(env={"ENVIRONMENT": "local"}).capture_bodies
        assert not TracingConfig(env={"ENVIRONMENT": "production"}).capture_bodies


class TestNoOpGuarantee:
    """With no LANGFUSE_* set, every tracing call must be a cheap no-op."""

    def test_configure_returns_false_without_keys(self) -> None:
        assert configure_tracing(TracingConfig(env={})) is False

    def test_span_context_manager_works_with_tracing_off(self) -> None:
        # The point: call sites never branch on whether tracing is enabled.
        request = user_request("hello")
        with llm_span(request, capture_bodies=False) as span:
            span.record_response(
                CompletionResponse(
                    text="hi",
                    provider="fake",
                    model="fake/v1",
                    usage=Usage(prompt_tokens=1, completion_tokens=1),
                    cost_usd=Decimal(0),
                    latency_ms=1.0,
                )
            )
        assert span.recorded

    def test_exception_propagates_and_is_not_swallowed(self) -> None:
        with pytest.raises(RuntimeError, match="boom"), llm_span(user_request("x")):
            msg = "boom"
            raise RuntimeError(msg)

    def test_helpers_are_safe_with_no_active_span(self) -> None:
        # These must not raise when tracing is off — they run on every request.
        record_retrieval(["a", "b"], scores=[0.9, 0.8], query="q")
        record_tool_call("search", call_id="1", arguments="{}", result="ok")
        record_tool_call("search", error="failed")

    def test_mismatched_scores_do_not_raise(self) -> None:
        # Misaligned scores would mislabel which chunk scored what; warn, not crash.
        record_retrieval(["a", "b"], scores=[0.9])

    def test_get_tracer_returns_a_usable_tracer(self) -> None:
        with get_tracer().start_as_current_span("t") as span:
            span.set_attribute("k", "v")

    def test_shutdown_is_safe_when_never_configured(self) -> None:
        shutdown_tracing()


class TestSemanticConventions:
    def test_genai_attributes_follow_the_spec(self) -> None:
        assert GenAI.USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
        assert GenAI.REQUEST_MODEL == "gen_ai.request.model"
        assert GenAI.OPERATION_NAME == "gen_ai.operation.name"

    def test_non_standard_attributes_are_namespaced(self) -> None:
        # `app.` prefix so our additions can never collide with a future semconv.
        for attr in (GenAI.COST_USD, GenAI.LATENCY_MS, GenAI.PROVIDER, GenAI.RETRIEVAL_IDS):
            assert attr.startswith("app.")
