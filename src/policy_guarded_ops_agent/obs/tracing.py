"""OpenTelemetry GenAI tracing, exported to Langfuse. Optional by construction.

Zero-account guarantee
----------------------
**This module no-ops cleanly when ``LANGFUSE_*`` is unset.** That is not a
fallback path bolted on afterwards — it is how the OpenTelemetry API is designed
to work. If no ``TracerProvider`` is installed, ``trace.get_tracer()`` returns a
non-recording tracer whose spans are cheap no-op objects. So:

* No keys  ⇒ no provider installed ⇒ spans cost ~nothing, nothing is exported,
  no network call is attempted, and every call site stays unchanged.
* Keys set ⇒ a provider is installed and spans flow to Langfuse over OTLP.

Call sites never branch on whether tracing is on. There is exactly one code path.

Conventions
-----------
Attributes follow the OpenTelemetry **GenAI semantic conventions**. The names are
declared as constants below rather than imported from
``opentelemetry.semconv._incubating``: that module is explicitly unstable and has
renamed attributes between minor releases. Pinning the strings here means an
upstream rename is a deliberate edit, not a silent change in what we emit.

Honesty note
------------
Everything recorded here is *measured* — token counts as reported by the
provider, wall-clock latency, and cost only when the price list is known. A
``None`` cost is recorded as absent, never as ``0``. Do not read a dashboard
average back into the README without also carrying split, model+version, temp,
seed, scaffold and CI. A trace is telemetry, not a benchmark.

Copy to ``src/<package>/obs/tracing.py``.
"""

from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Final

import structlog
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from decimal import Decimal

    from policy_guarded_ops_agent.llm.gateway import CompletionRequest, CompletionResponse

__all__ = [
    "GenAI",
    "TracingConfig",
    "configure_tracing",
    "get_tracer",
    "llm_span",
    "record_retrieval",
    "record_tool_call",
    "shutdown_tracing",
]

log: Final = structlog.get_logger(__name__)

_TRACER_NAME: Final = "genai.gateway"
#: Langfuse's OTLP ingestion path (Langfuse v3+).
_LANGFUSE_OTLP_PATH: Final = "/api/public/otel/v1/traces"
#: Cap on how much prompt/completion text a span may carry. Bodies are the
#: expensive part of an observability bill and the risky part of a privacy
#: review; truncate rather than ship unbounded user text.
_MAX_BODY_CHARS: Final = 4_000


class GenAI:
    """OpenTelemetry GenAI semantic-convention attribute names.

    Pinned deliberately — see the module docstring.
    """

    SYSTEM: Final = "gen_ai.system"
    OPERATION_NAME: Final = "gen_ai.operation.name"
    REQUEST_MODEL: Final = "gen_ai.request.model"
    REQUEST_TEMPERATURE: Final = "gen_ai.request.temperature"
    REQUEST_MAX_TOKENS: Final = "gen_ai.request.max_tokens"
    REQUEST_SEED: Final = "gen_ai.request.seed"
    RESPONSE_MODEL: Final = "gen_ai.response.model"
    RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
    USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
    USAGE_CACHED_INPUT_TOKENS: Final = "gen_ai.usage.cached_input_tokens"
    TOOL_NAME: Final = "gen_ai.tool.name"
    TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
    PROMPT: Final = "gen_ai.prompt"
    COMPLETION: Final = "gen_ai.completion"

    # --- Non-standard attributes. Namespaced under `app.` so they are visibly
    # ours and can never collide with a future semconv addition. ---
    COST_USD: Final = "app.gen_ai.cost_usd"
    LATENCY_MS: Final = "app.gen_ai.latency_ms"
    PROVIDER: Final = "app.gen_ai.provider"
    ATTEMPTS: Final = "app.gen_ai.attempts"
    FALLBACK_PATH: Final = "app.gen_ai.fallback_path"
    CACHE_HIT: Final = "app.gen_ai.cache_hit"
    RETRIEVAL_COUNT: Final = "app.retrieval.chunk_count"
    RETRIEVAL_IDS: Final = "app.retrieval.chunk_ids"
    RETRIEVAL_SCORES: Final = "app.retrieval.scores"

    # Langfuse reads these to populate session/user grouping.
    LANGFUSE_SESSION_ID: Final = "langfuse.session.id"
    LANGFUSE_USER_ID: Final = "langfuse.user.id"


class TracingConfig:
    """Tracing configuration resolved from the environment.

    ``enabled`` is True only when both Langfuse keys are present. Everything
    downstream keys off that single fact.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        source = os.environ if env is None else env
        self.public_key = source.get("LANGFUSE_PUBLIC_KEY", "").strip()
        self.secret_key = source.get("LANGFUSE_SECRET_KEY", "").strip()
        self.host = source.get("LANGFUSE_HOST", "https://cloud.langfuse.com").strip().rstrip("/")
        self.service_name = source.get("OTEL_SERVICE_NAME", "app").strip() or "app"
        self.environment = source.get("ENVIRONMENT", "local").strip() or "local"
        self.sample_rate = self._parse_rate(source.get("LANGFUSE_SAMPLE_RATE", "1.0"))
        #: Whether prompt/completion bodies are attached to spans. Off outside
        #: local by default: bodies may contain user data, and the free tier's
        #: 50k observations/mo goes further without them.
        self.capture_bodies = source.get(
            "LANGFUSE_CAPTURE_BODIES", "true" if self.environment == "local" else "false"
        ).strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _parse_rate(raw: str) -> float:
        """Parse the sample rate, clamping to [0, 1]. A bad value must not crash the app."""
        try:
            return min(max(float(raw), 0.0), 1.0)
        except ValueError:
            log.warning("invalid_sample_rate", value=raw, using=1.0)
            return 1.0

    @property
    def enabled(self) -> bool:
        """True only when Langfuse is fully configured."""
        return bool(self.public_key and self.secret_key)

    @property
    def otlp_endpoint(self) -> str:
        """Full OTLP traces endpoint for the configured Langfuse host."""
        return f"{self.host}{_LANGFUSE_OTLP_PATH}"

    @property
    def otlp_headers(self) -> dict[str, str]:
        """Basic-auth header for Langfuse OTLP ingestion."""
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}


_configured = False


def configure_tracing(config: TracingConfig | None = None) -> bool:
    """Install a TracerProvider exporting to Langfuse, if configured.

    Safe and cheap to call unconditionally at startup — that is the intended use.
    Idempotent: repeat calls are no-ops.

    Returns:
        True when tracing was activated; False when it is off (no keys, or the
        optional ``obs`` extra is not installed). False is a normal, supported
        state, not an error.
    """
    global _configured  # noqa: PLW0603 — process-wide provider is a singleton by design.
    if _configured:
        return trace.get_tracer_provider().__class__.__name__ != "NoOpTracerProvider"

    cfg = config if config is not None else TracingConfig()
    if not cfg.enabled:
        # No provider installed => the OTel API hands out non-recording spans.
        # This is the zero-account path and it costs nothing.
        log.info("tracing_disabled", reason="LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
        _configured = True
        return False

    try:
        # Lazy imports (PLC0415 waived deliberately): these live in the optional
        # `obs` extra. Importing them at module scope would make the extra a hard
        # requirement and break the zero-account path, which is the whole point
        # of this module. The ImportError branch below is the supported state.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
        from opentelemetry.sdk.trace.sampling import (  # noqa: PLC0415
            ParentBased,
            TraceIdRatioBased,
        )
    except ImportError:
        log.warning(
            "tracing_unavailable",
            reason="optional extra not installed",
            fix="uv sync --extra obs",
        )
        _configured = True
        return False

    resource = Resource.create(
        {
            "service.name": cfg.service_name,
            "deployment.environment.name": cfg.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(root=TraceIdRatioBased(cfg.sample_rate)),
    )
    provider.add_span_processor(
        # Batched: an export must never sit in a request's critical path.
        BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg.otlp_endpoint, headers=cfg.otlp_headers))
    )
    trace.set_tracer_provider(provider)
    _configured = True
    log.info("tracing_enabled", endpoint=cfg.otlp_endpoint, sample_rate=cfg.sample_rate)
    return True


def shutdown_tracing() -> None:
    """Flush pending spans. Call on shutdown so the last traces are not dropped.

    Harmless when tracing was never enabled.
    """
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


def get_tracer() -> trace.Tracer:
    """Return the tracer. Non-recording when tracing is off."""
    return trace.get_tracer(_TRACER_NAME)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return f"{text[:_MAX_BODY_CHARS]}…[truncated {len(text) - _MAX_BODY_CHARS} chars]"


@contextmanager
def llm_span(
    request: CompletionRequest,
    *,
    operation: str = "chat",
    system: str = "litellm",
    session_id: str | None = None,
    user_id: str | None = None,
    capture_bodies: bool | None = None,
) -> Iterator[_LLMSpanRecorder]:
    """Trace one LLM request.

    Records the request up front, and the caller feeds the response back via
    :meth:`_LLMSpanRecorder.record_response` so tokens, cost and latency land on
    the same span. Exceptions are recorded and re-raised, never swallowed.

    Example::

        with llm_span(request) as span:
            response = await gateway.acomplete(request)
            span.record_response(response)

    When tracing is disabled every method below is a no-op on a non-recording
    span — the ``with`` block behaves identically, minus the export.
    """
    cfg = TracingConfig()
    include_bodies = cfg.capture_bodies if capture_bodies is None else capture_bodies
    tracer = get_tracer()

    with tracer.start_as_current_span(
        f"{operation} {request.messages[-1].role if request.messages else 'empty'}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute(GenAI.SYSTEM, system)
        span.set_attribute(GenAI.OPERATION_NAME, operation)
        span.set_attribute(GenAI.REQUEST_TEMPERATURE, request.temperature)
        if request.max_tokens is not None:
            span.set_attribute(GenAI.REQUEST_MAX_TOKENS, request.max_tokens)
        if request.seed is not None:
            span.set_attribute(GenAI.REQUEST_SEED, request.seed)
        if session_id is not None:
            span.set_attribute(GenAI.LANGFUSE_SESSION_ID, session_id)
        if user_id is not None:
            span.set_attribute(GenAI.LANGFUSE_USER_ID, user_id)
        for key, value in request.metadata.items():
            span.set_attribute(f"app.meta.{key}", value)
        if include_bodies:
            span.set_attribute(
                GenAI.PROMPT,
                _truncate("\n".join(f"{m.role}: {m.content}" for m in request.messages)),
            )

        recorder = _LLMSpanRecorder(span, include_bodies=include_bodies)
        try:
            yield recorder
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            if not recorder.recorded:
                # A span that claims success but carries no usage is a lie by
                # omission on a cost dashboard. Mark it explicitly.
                # FBT003: `False` is the attribute's VALUE in the OTel API, not a
                # boolean flag argument — the rule does not apply here.
                span.set_attribute("app.gen_ai.response_recorded", False)  # noqa: FBT003


class _LLMSpanRecorder:
    """Attaches response telemetry to an in-flight span."""

    def __init__(self, span: Span, *, include_bodies: bool) -> None:
        self._span = span
        self._include_bodies = include_bodies
        self._recorded = False

    @property
    def recorded(self) -> bool:
        """Whether a response has been recorded on this span."""
        return self._recorded

    @property
    def span(self) -> Span:
        """The underlying span, for callers needing custom attributes."""
        return self._span

    def record_response(self, response: CompletionResponse) -> None:
        """Record tokens, cost, latency and routing outcome from a gateway response."""
        self._recorded = True
        span = self._span
        span.set_attribute(GenAI.RESPONSE_MODEL, response.model)
        span.set_attribute(GenAI.PROVIDER, response.provider)
        span.set_attribute(GenAI.USAGE_INPUT_TOKENS, response.usage.prompt_tokens)
        span.set_attribute(GenAI.USAGE_OUTPUT_TOKENS, response.usage.completion_tokens)
        span.set_attribute(GenAI.USAGE_CACHED_INPUT_TOKENS, response.usage.cached_prompt_tokens)
        span.set_attribute(GenAI.LATENCY_MS, response.latency_ms)
        span.set_attribute(GenAI.ATTEMPTS, response.attempts)
        span.set_attribute(GenAI.CACHE_HIT, response.cache_hit)
        if response.finish_reason is not None:
            span.set_attribute(GenAI.RESPONSE_FINISH_REASONS, [response.finish_reason])
        if response.fallback_path:
            span.set_attribute(GenAI.FALLBACK_PATH, list(response.fallback_path))
        # Absent, not zero, when pricing is unknown. A zero here would quietly
        # under-report spend on every dashboard downstream.
        if response.cost_usd is not None:
            span.set_attribute(GenAI.COST_USD, float(response.cost_usd))
        if self._include_bodies:
            span.set_attribute(GenAI.COMPLETION, _truncate(response.text))
        span.set_status(Status(StatusCode.OK))

    def record_cost(self, cost_usd: Decimal | None) -> None:
        """Override cost when the caller has better information than the price list."""
        if cost_usd is not None:
            self._span.set_attribute(GenAI.COST_USD, float(cost_usd))


def record_retrieval(
    chunk_ids: Sequence[str],
    *,
    scores: Sequence[float] | None = None,
    query: str | None = None,
    span: Span | None = None,
) -> None:
    """Record retrieved chunks on the current span.

    Attaches ids and similarity scores, not chunk bodies — bodies blow past the
    free tier's observation quota and are recoverable from the ids anyway.

    Args:
        chunk_ids: Ids of retrieved chunks, in rank order.
        scores: Similarity scores aligned to ``chunk_ids``.
        query: The retrieval query. Recorded as an event, not an attribute.
        span: Target span. Defaults to the current span.
    """
    target = span if span is not None else trace.get_current_span()
    target.set_attribute(GenAI.RETRIEVAL_COUNT, len(chunk_ids))
    target.set_attribute(GenAI.RETRIEVAL_IDS, list(chunk_ids))
    if scores is not None:
        if len(scores) != len(chunk_ids):
            # Misaligned scores would silently mislabel which chunk scored what.
            log.warning("retrieval_score_mismatch", ids=len(chunk_ids), scores=len(scores))
        else:
            target.set_attribute(GenAI.RETRIEVAL_SCORES, list(scores))
    attributes: dict[str, Any] = {"chunk_count": len(chunk_ids)}
    if query is not None:
        attributes["query"] = _truncate(query)
    target.add_event("retrieval", attributes=attributes)


def record_tool_call(
    name: str,
    *,
    call_id: str | None = None,
    arguments: str | None = None,
    result: str | None = None,
    error: str | None = None,
    span: Span | None = None,
) -> None:
    """Record a tool invocation as an event on the current span.

    An event rather than a child span: tool calls are frequent and the free tier
    bills per observation, so this keeps a multi-tool agent turn affordable.
    """
    target = span if span is not None else trace.get_current_span()
    attributes: dict[str, Any] = {GenAI.TOOL_NAME: name}
    if call_id is not None:
        attributes[GenAI.TOOL_CALL_ID] = call_id
    if arguments is not None:
        attributes["arguments"] = _truncate(arguments)
    if result is not None:
        attributes["result"] = _truncate(result)
    if error is not None:
        attributes["error"] = _truncate(error)
        target.set_status(Status(StatusCode.ERROR, f"tool {name} failed"))
    target.add_event("tool_call", attributes=attributes)
