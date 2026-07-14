"""LiteLLM-backed LLM gateway with free-tier providers, fallback and resilience.

Design contract
---------------
* **Deterministic code does the work; the LLM only decides.** Everything here —
  routing, retry, budget, rate limiting, validation — is plain Python. The model
  is never asked to manage its own reliability.
* **No API key required to import, construct, or unit-test.** Inject any object
  satisfying :class:`CompletionBackend`; ``fakes/fake_llm.py`` ships a
  deterministic one. This is what keeps CI free.
* **Never fabricate a cost.** :attr:`CompletionResponse.cost_usd` is ``None``
  whenever pricing is unknown. It is ``Decimal("0")`` only for providers whose
  free tier is genuinely unmetered — that is a fact about the price list, not an
  estimate, and it is not a benchmark number.
* **No regex-parsed JSON.** Structured output goes through
  :meth:`Gateway.acomplete_model`, which asks for a JSON schema and validates
  with Pydantic. A malformed body raises; it is never salvaged by pattern match.

Copy this file to ``src/<package>/llm/gateway.py``. It imports nothing from a
sibling project.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypeVar, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from types import TracebackType

__all__ = [
    "FREE_TIER_PROVIDERS",
    "AllProvidersFailedError",
    "BudgetExceededError",
    "BudgetLedger",
    "ChatMessage",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "CompletionBackend",
    "CompletionRequest",
    "CompletionResponse",
    "Gateway",
    "GatewayError",
    "LiteLLMBackend",
    "PriceSpec",
    "ProviderError",
    "ProviderSpec",
    "StructuredOutputError",
    "TokenBucket",
    "Usage",
    "build_default_chain",
]

log: Final = structlog.get_logger(__name__)

#: Exponential backoff schedule in seconds, applied on 429/5xx. Four retries:
#: 1s, 2s, 4s, 8s. Full jitter is added per attempt to avoid thundering herds
#: against a shared free-tier quota.
BACKOFF_SCHEDULE_S: Final[tuple[float, ...]] = (1.0, 2.0, 4.0, 8.0)

_TOKENS_PER_MILLION: Final = Decimal(1_000_000)
_DEFAULT_TIMEOUT_S: Final = 30.0
_IDEMPOTENCY_CACHE_MAX: Final = 512

# Retryable upstream statuses. 429 = rate limited (expected on a free tier);
# 5xx = provider-side fault. Everything else (400/401/403/404) is our bug or a
# bad key, and retrying it just wastes the quota that caused the problem.
_HTTP_TOO_MANY_REQUESTS: Final = 429
_HTTP_SERVER_ERROR_MIN: Final = 500
_HTTP_SERVER_ERROR_MAX: Final = 600

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

TModel = TypeVar("TModel", bound=BaseModel)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GatewayError(Exception):
    """Base class for every error raised by this module."""


class ProviderError(GatewayError):
    """A single provider attempt failed.

    Args:
        message: Human-readable cause.
        provider: Provider name that failed.
        status_code: Upstream HTTP status, when one was available.
        retryable: Whether retrying the *same* provider could plausibly succeed.
            429 and 5xx are retryable; 400/401/403/404 are not.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


class AllProvidersFailedError(GatewayError):
    """Every provider in the fallback chain failed.

    Carries the per-provider causes so the caller can log precisely why the
    chain was exhausted rather than guessing.
    """

    def __init__(self, failures: Mapping[str, str]) -> None:
        self.failures = dict(failures)
        detail = "; ".join(f"{name}: {cause}" for name, cause in self.failures.items())
        super().__init__(f"all providers failed ({detail or 'chain was empty'})")


class BudgetExceededError(GatewayError):
    """The virtual key's spend ceiling would be breached by this request."""

    def __init__(self, *, spent: Decimal, limit: Decimal) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(f"budget exceeded: spent ${spent} of ${limit} limit")


class CircuitOpenError(ProviderError):
    """The circuit breaker is open for this provider; the call was not attempted."""

    def __init__(self, provider: str, *, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(
            f"circuit open for {provider}, retry in {retry_after_s:.1f}s",
            provider=provider,
            retryable=False,
        )


class StructuredOutputError(GatewayError):
    """The model returned a body that does not validate against the target schema."""

    def __init__(self, message: str, *, raw: str) -> None:
        self.raw = raw
        super().__init__(message)


# ---------------------------------------------------------------------------
# Wire types
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """One message in a chat completion request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str
    cacheable: bool = Field(
        default=False,
        description=(
            "Mark this block for prompt caching. Honoured only where the provider "
            "supports it (ProviderSpec.supports_prompt_cache); silently ignored "
            "elsewhere, which is safe — caching is an optimisation, not semantics."
        ),
    )


class Usage(BaseModel):
    """Token accounting for one completion, as reported by the provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = Field(
        default=0,
        description="Subset of prompt_tokens served from the provider's prompt cache.",
    )

    @property
    def total_tokens(self) -> int:
        """Total billable-ish token count. Prompt tokens include cached ones."""
        return self.prompt_tokens + self.completion_tokens


class PriceSpec(BaseModel):
    """Per-provider price list.

    ``None`` means *unknown*, and propagates to ``cost_usd=None``. It never
    degrades into a guess. ``Decimal("0")`` is reserved for tiers that are
    genuinely unmetered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    usd_per_1m_input: Decimal | None = None
    usd_per_1m_output: Decimal | None = None
    #: True only when the provider bills nothing at all for this model on its
    #: free tier. Free tiers are rate-limited rather than metered.
    is_free_tier: bool = False

    def cost_for(self, usage: Usage) -> Decimal | None:
        """Compute USD cost for ``usage``, or ``None`` when pricing is unknown."""
        if self.is_free_tier:
            return Decimal(0)
        if self.usd_per_1m_input is None or self.usd_per_1m_output is None:
            return None
        billable_input = max(usage.prompt_tokens - usage.cached_prompt_tokens, 0)
        return (
            Decimal(billable_input) * self.usd_per_1m_input
            + Decimal(usage.completion_tokens) * self.usd_per_1m_output
        ) / _TOKENS_PER_MILLION


class ProviderSpec(BaseModel):
    """A single entry in the fallback chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: LiteLLM model id, e.g. "groq/llama-3.3-70b-versatile".
    model: str
    #: Env var holding the key. ``None`` for keyless providers (e.g. local Ollama).
    api_key_env: str | None = None
    #: Per-provider request-per-minute ceiling. Free tiers are strict; staying
    #: under the published limit is cheaper than being throttled.
    rpm: int = 30
    #: Burst allowance for the token bucket.
    burst: int = 5
    supports_prompt_cache: bool = False
    price: PriceSpec = PriceSpec()
    api_base: str | None = None

    def is_available(self, env: Mapping[str, str] | None = None) -> bool:
        """True when this provider's credential is present (or none is needed)."""
        if self.api_key_env is None:
            return True
        source = os.environ if env is None else env
        return bool(source.get(self.api_key_env, "").strip())


class CompletionRequest(BaseModel):
    """A provider-agnostic completion request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[ChatMessage, ...]
    #: Default 0.0 — reproducibility beats variety for an eval-driven repo.
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    #: Passed through where supported. Not all providers honour it; a seed is a
    #: hint, never a guarantee of determinism. Report it alongside any number.
    seed: int | None = 0
    stop: tuple[str, ...] = ()
    #: JSON schema for structured output. Set by acomplete_model(); avoid setting
    #: it by hand.
    json_schema: dict[str, Any] | None = None
    #: Dedupe key. Two requests with the same key return the same response
    #: without a second provider call, making client-side retries safe.
    idempotency_key: str | None = None
    #: Free-form tags forwarded to tracing (never to the provider).
    metadata: Mapping[str, str] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    """A completed response, annotated with everything needed to report it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    provider: str
    #: Resolved model id, as reported by the provider. Report this, not the
    #: requested alias — they differ when a provider silently re-routes.
    model: str
    usage: Usage
    #: ``None`` when pricing is unknown. Never a guess.
    cost_usd: Decimal | None
    latency_ms: float
    finish_reason: str | None = None
    #: True when the provider reported a prompt-cache hit.
    cache_hit: bool = False
    #: Total provider attempts across the whole chain, including retries.
    attempts: int = 1
    #: Providers that failed before this one succeeded, in order.
    fallback_path: tuple[str, ...] = ()


class BackendResult(BaseModel):
    """Raw result from a :class:`CompletionBackend`, before gateway annotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model: str
    usage: Usage = Usage()
    finish_reason: str | None = None
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CompletionBackend(Protocol):
    """Transport for one completion attempt.

    Implementations MUST raise :class:`ProviderError` with an accurate
    ``retryable`` flag; the gateway's retry logic trusts it. Any other exception
    is treated as non-retryable and fails over to the next provider.
    """

    async def acomplete(
        self,
        spec: ProviderSpec,
        request: CompletionRequest,
        *,
        timeout_s: float,
    ) -> BackendResult:
        """Perform one attempt against ``spec``. No retries here — the gateway owns them."""
        ...


# ---------------------------------------------------------------------------
# Free-tier provider registry
# ---------------------------------------------------------------------------

#: Providers with a free tier that needs no credit card.
#:
#: RPM values are conservative floors taken from each provider's published free
#: limits at time of writing; they are throttling hints, not measurements, and
#: providers change them without notice. Verify against the live docs before
#: quoting any of this in a report.
#:
#: Pricing is marked ``is_free_tier=True``: these tiers are rate-limited rather
#: than metered, so marginal cost is genuinely $0. That is a property of the
#: price list — it is NOT a benchmark result and must not be presented as one.
FREE_TIER_PROVIDERS: Final[Mapping[str, ProviderSpec]] = {
    "groq": ProviderSpec(
        name="groq",
        model="groq/llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        rpm=30,
        burst=5,
        supports_prompt_cache=False,
        price=PriceSpec(is_free_tier=True),
    ),
    "gemini": ProviderSpec(
        name="gemini",
        model="gemini/gemini-2.0-flash",
        api_key_env="GEMINI_API_KEY",
        rpm=15,
        burst=3,
        # Gemini supports context caching; LiteLLM surfaces it via cache_control.
        supports_prompt_cache=True,
        price=PriceSpec(is_free_tier=True),
    ),
    "cerebras": ProviderSpec(
        name="cerebras",
        model="cerebras/llama-3.3-70b",
        api_key_env="CEREBRAS_API_KEY",
        rpm=30,
        burst=5,
        supports_prompt_cache=False,
        price=PriceSpec(is_free_tier=True),
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        # The `:free` suffix is load-bearing. Drop it and this stops being free.
        model="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        api_key_env="OPENROUTER_API_KEY",
        rpm=20,
        burst=3,
        supports_prompt_cache=False,
        price=PriceSpec(is_free_tier=True),
    ),
    # OPTIONAL. Requires a local Ollama daemon; keyless, hence always "available"
    # if you put it in a chain. Excluded from the default chain on purpose: the
    # reference machine (Intel UHD 620, no CUDA, 4 cores) cannot run this fast
    # enough to benchmark. Treat as a documented extra, never a dependency.
    "ollama": ProviderSpec(
        name="ollama",
        model="ollama/llama3.2",
        api_key_env=None,
        rpm=60,
        burst=10,
        price=PriceSpec(is_free_tier=True),
        api_base="http://localhost:11434",
    ),
}


def build_default_chain(env: Mapping[str, str] | None = None) -> tuple[ProviderSpec, ...]:
    """Build the fallback chain from the environment.

    Honours ``LLM_FALLBACK_CHAIN`` (comma-separated provider names) when set;
    otherwise uses a sensible free-tier order. Providers whose key is absent are
    dropped, so a partially configured environment still works.

    Returns an empty tuple when nothing is configured — the caller should then
    fall back to the deterministic fake. This is the zero-account path, and it
    is a supported state, not an error.
    """
    source = os.environ if env is None else env
    raw = source.get("LLM_FALLBACK_CHAIN", "").strip()
    names: Sequence[str] = (
        [n.strip() for n in raw.split(",") if n.strip()]
        if raw
        # Groq first (fastest free tier), then Gemini, Cerebras, OpenRouter.
        else ["groq", "gemini", "cerebras", "openrouter"]
    )
    chain = []
    for name in names:
        spec = FREE_TIER_PROVIDERS.get(name)
        if spec is None:
            log.warning("unknown_provider_in_chain", provider=name)
            continue
        if spec.is_available(source):
            chain.append(spec)
    return tuple(chain)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TokenBucket:
    """Async token bucket, one per provider.

    Free tiers publish hard RPM ceilings. Shaping locally is strictly better
    than discovering the limit via 429s, which burn wall-clock in backoff.
    """

    def __init__(
        self,
        *,
        rate_per_minute: int,
        burst: int,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
    ) -> None:
        if rate_per_minute <= 0:
            msg = "rate_per_minute must be positive"
            raise ValueError(msg)
        self._rate_per_s = rate_per_minute / 60.0
        self._capacity = float(max(burst, 1))
        self._tokens = self._capacity
        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    @property
    def available(self) -> float:
        """Tokens currently in the bucket, without refilling. For tests/metrics."""
        return self._tokens

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._updated, 0.0)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_s)
        self._updated = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available. Returns seconds spent waiting."""
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                delay = deficit / self._rate_per_s
                waited += delay
                await self._sleep(delay)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker.

    Opens after ``failure_threshold`` consecutive failures, stays open for
    ``recovery_timeout_s``, then admits a single probe (half-open). A successful
    probe closes it; a failed probe re-opens it for another full timeout.

    Without this, a dead free-tier provider costs every request its full retry
    schedule (1+2+4+8 = 15s) before failing over. With it, the chain skips
    straight to the next provider.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for elapsed recovery time."""
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at >= self._recovery_timeout_s:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    @property
    def retry_after_s(self) -> float:
        """Seconds until the next probe is admitted. 0.0 when not open."""
        if self._opened_at is None:
            return 0.0
        remaining = self._recovery_timeout_s - (self._clock() - self._opened_at)
        return max(remaining, 0.0)

    def allow(self) -> bool:
        """Whether a call may proceed. Admits exactly one probe when half-open."""
        state = self.state
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.HALF_OPEN and not self._half_open_in_flight:
            self._half_open_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        """Reset the breaker."""
        self._failures = 0
        self._opened_at = None
        self._half_open_in_flight = False

    def record_failure(self) -> None:
        """Count a failure, opening the circuit at the threshold."""
        self._half_open_in_flight = False
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = self._clock()


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class BudgetLedger:
    """Virtual-key spend ceiling.

    Free tiers price at $0, so in the intended configuration this never trips.
    It exists as a tripwire: if someone swaps in a metered provider, the ledger
    stops the bleeding instead of discovering it on a bill.

    Requests whose cost is unknown (``cost_usd is None``) are recorded as $0 and
    counted separately. An unknown cost is never guessed — see
    :attr:`unpriced_calls`, and treat a non-zero value as "this ledger is blind".
    """

    def __init__(self, *, limit_usd: Decimal, key: str = "default") -> None:
        self._limit = limit_usd
        self._key = key
        self._spent = Decimal(0)
        self._unpriced = 0
        self._lock = asyncio.Lock()

    @property
    def key(self) -> str:
        """Virtual key this ledger tracks."""
        return self._key

    @property
    def spent_usd(self) -> Decimal:
        """Total known spend."""
        return self._spent

    @property
    def limit_usd(self) -> Decimal:
        """Configured ceiling."""
        return self._limit

    @property
    def unpriced_calls(self) -> int:
        """Calls whose cost could not be determined. Non-zero ⇒ spend is under-counted."""
        return self._unpriced

    async def check(self) -> None:
        """Raise :class:`BudgetExceededError` if the ceiling is already reached."""
        async with self._lock:
            # A zero limit is the free-tier default and means "$0 of paid spend
            # is allowed", not "no budget checking". Free calls cost 0 and so
            # never exceed it.
            if self._spent > self._limit:
                raise BudgetExceededError(spent=self._spent, limit=self._limit)

    async def record(self, cost: Decimal | None) -> None:
        """Add ``cost`` to the ledger. ``None`` increments :attr:`unpriced_calls`."""
        async with self._lock:
            if cost is None:
                self._unpriced += 1
                return
            self._spent += cost


# ---------------------------------------------------------------------------
# LiteLLM backend
# ---------------------------------------------------------------------------


def _classify_status(status: int | None) -> bool:
    """Return whether an upstream status is worth retrying on the same provider."""
    if status is None:
        return False
    return status == _HTTP_TOO_MANY_REQUESTS or (
        _HTTP_SERVER_ERROR_MIN <= status < _HTTP_SERVER_ERROR_MAX
    )


def _extract_status(exc: BaseException) -> int | None:
    """Best-effort status extraction across LiteLLM/httpx exception shapes."""
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class LiteLLMBackend:
    """:class:`CompletionBackend` backed by ``litellm.acompletion``.

    LiteLLM is imported lazily so that importing this module — and running the
    entire fake-backed test suite — costs nothing and requires no key.
    """

    def __init__(self, *, drop_unsupported_params: bool = True) -> None:
        #: LiteLLM's `drop_params` strips parameters a given provider rejects
        #: (e.g. `seed` on providers that lack it) instead of erroring. Keeps one
        #: request shape working across a heterogeneous fallback chain.
        self._drop_params = drop_unsupported_params

    @staticmethod
    def _render_messages(
        request: CompletionRequest,
        spec: ProviderSpec,
    ) -> list[dict[str, Any]]:
        """Convert to LiteLLM wire format, applying prompt caching where supported.

        Where the provider supports caching and a message is marked cacheable, the
        content is emitted as a parts list carrying ``cache_control``. Elsewhere
        it stays a plain string — the flag is dropped, never faked.
        """
        rendered: list[dict[str, Any]] = []
        for message in request.messages:
            if message.cacheable and spec.supports_prompt_cache:
                rendered.append(
                    {
                        "role": message.role,
                        "content": [
                            {
                                "type": "text",
                                "text": message.content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            else:
                rendered.append({"role": message.role, "content": message.content})
        return rendered

    @staticmethod
    def _parse_usage(raw: object) -> Usage:
        """Read usage off a LiteLLM response without trusting its shape."""
        if raw is None:
            return Usage()
        prompt = getattr(raw, "prompt_tokens", 0) or 0
        completion = getattr(raw, "completion_tokens", 0) or 0
        cached = 0
        details = getattr(raw, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        return Usage(
            prompt_tokens=int(prompt),
            completion_tokens=int(completion),
            cached_prompt_tokens=int(cached),
        )

    async def acomplete(
        self,
        spec: ProviderSpec,
        request: CompletionRequest,
        *,
        timeout_s: float,
    ) -> BackendResult:
        """Perform one LiteLLM completion attempt against ``spec``."""
        # Lazy import (PLC0415 waived deliberately): litellm costs ~2s to import
        # and pulls a large dependency tree. Importing it at module scope would
        # tax every test run and every fake-backed eval — the paths that must stay
        # fast and key-free. Nothing above this line needs litellm.
        import litellm  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "model": spec.model,
            "messages": self._render_messages(request, spec),
            "temperature": request.temperature,
            "timeout": timeout_s,
            "num_retries": 0,  # Retry policy lives in the Gateway, not LiteLLM.
            "drop_params": self._drop_params,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.seed is not None:
            kwargs["seed"] = request.seed
        if request.stop:
            kwargs["stop"] = list(request.stop)
        if spec.api_base is not None:
            kwargs["api_base"] = spec.api_base
        if request.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": request.json_schema,
                    "strict": True,
                },
            }

        try:
            response = await litellm.acompletion(**kwargs)
        except TimeoutError as exc:
            msg = f"{spec.name} timed out after {timeout_s}s"
            raise ProviderError(msg, provider=spec.name, retryable=True) from exc
        except Exception as exc:
            # LiteLLM surfaces provider SDK exceptions of many shapes. Classify by
            # status where we can find one, and treat anything unrecognised as
            # non-retryable so an unknown 400 is not retried four times.
            status = _extract_status(exc)
            msg = f"{spec.name} call failed: {exc}"
            raise ProviderError(
                msg,
                provider=spec.name,
                status_code=status,
                retryable=_classify_status(status),
            ) from exc

        try:
            choice = response.choices[0]
            text = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
        except (AttributeError, IndexError, KeyError) as exc:
            msg = f"{spec.name} returned an unreadable response shape: {exc}"
            raise ProviderError(msg, provider=spec.name, retryable=False) from exc

        usage = self._parse_usage(getattr(response, "usage", None))
        return BackendResult(
            text=text,
            model=str(getattr(response, "model", spec.model)),
            usage=usage,
            finish_reason=finish_reason,
            cache_hit=usage.cached_prompt_tokens > 0,
        )


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class Gateway:
    """Routes completions across a free-tier fallback chain with full resilience.

    Per provider, in order: budget check → circuit check → rate limit → attempt,
    with 1/2/4/8s jittered backoff on 429/5xx. Non-retryable failures fail over
    immediately. When every provider is exhausted, raises
    :class:`AllProvidersFailedError` carrying each cause.

    Example — no key, no network, fully deterministic::

        from policy_guarded_ops_agent.fakes.fake_llm import FakeLLMBackend, fake_provider_spec

        gateway = Gateway(chain=[fake_provider_spec()], backend=FakeLLMBackend())
        response = await gateway.acomplete(
            CompletionRequest(messages=(ChatMessage(role="user", content="ping"),))
        )
    """

    def __init__(
        self,
        *,
        chain: Sequence[ProviderSpec],
        backend: CompletionBackend | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        budget: BudgetLedger | None = None,
        backoff_schedule: Sequence[float] = BACKOFF_SCHEDULE_S,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        if not chain:
            msg = (
                "chain must contain at least one provider. With no API keys set, "
                "build one from fake_provider_spec() in "
                "policy_guarded_ops_agent.fakes.fake_llm — that is the "
                "supported zero-account path."
            )
            raise ValueError(msg)
        self._chain = tuple(chain)
        self._backend: CompletionBackend = backend if backend is not None else LiteLLMBackend()
        self._timeout_s = timeout_s
        self._budget = budget
        self._backoff = tuple(backoff_schedule)
        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        # Full jitter: sleep uniformly in [0, delay]. Injectable so tests are
        # deterministic. S311 is not applicable — this is scheduling, not crypto.
        self._jitter: Callable[[float], float] = (
            jitter if jitter is not None else lambda delay: random.uniform(0.0, delay)  # noqa: S311
        )
        self._buckets: dict[str, TokenBucket] = {
            spec.name: TokenBucket(
                rate_per_minute=spec.rpm,
                burst=spec.burst,
                clock=clock,
                sleeper=self._sleep,
            )
            for spec in self._chain
        }
        self._breakers: dict[str, CircuitBreaker] = {
            spec.name: CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout_s=recovery_timeout_s,
                clock=clock,
            )
            for spec in self._chain
        }
        self._idempotency: dict[str, CompletionResponse] = {}

    @property
    def chain(self) -> tuple[ProviderSpec, ...]:
        """The configured fallback chain, in priority order."""
        return self._chain

    def circuit_state(self, provider: str) -> CircuitState:
        """Current circuit state for ``provider``. Useful for health endpoints."""
        breaker = self._breakers.get(provider)
        if breaker is None:
            msg = f"unknown provider: {provider}"
            raise KeyError(msg)
        return breaker.state

    async def __aenter__(self) -> Gateway:
        """Support ``async with`` for symmetry with resource-holding backends."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> Literal[False]:
        """Teardown. Holds no sockets of its own, so nothing to close.

        Returning False never suppresses the exception in flight.
        """
        return False

    def _cache_get(self, key: str | None) -> CompletionResponse | None:
        return self._idempotency.get(key) if key is not None else None

    def _cache_put(self, key: str | None, response: CompletionResponse) -> None:
        if key is None:
            return
        if len(self._idempotency) >= _IDEMPOTENCY_CACHE_MAX:
            # Bounded FIFO eviction; this is a retry-dedupe window, not a cache.
            self._idempotency.pop(next(iter(self._idempotency)))
        self._idempotency[key] = response

    async def _attempt_provider(
        self,
        spec: ProviderSpec,
        request: CompletionRequest,
    ) -> tuple[BackendResult, float, int]:
        """Try one provider with the full backoff schedule.

        Returns ``(result, latency_ms, attempts)``. Raises :class:`ProviderError`
        when the provider is exhausted or fails non-retryably.
        """
        breaker = self._breakers[spec.name]
        if not breaker.allow():
            raise CircuitOpenError(spec.name, retry_after_s=breaker.retry_after_s)

        last_error: ProviderError | None = None
        # len(backoff) retries after the first try.
        for attempt_index in range(len(self._backoff) + 1):
            await self._buckets[spec.name].acquire()
            attempts = attempt_index + 1
            started = self._clock()
            try:
                result = await self._backend.acomplete(spec, request, timeout_s=self._timeout_s)
            except ProviderError as exc:
                last_error = exc
                breaker.record_failure()
                if not exc.retryable or attempt_index >= len(self._backoff):
                    raise
                delay = self._jitter(self._backoff[attempt_index])
                log.warning(
                    "provider_retry",
                    provider=spec.name,
                    attempt=attempts,
                    status_code=exc.status_code,
                    sleep_s=round(delay, 3),
                )
                await self._sleep(delay)
            except Exception as exc:
                # A backend that raises something other than ProviderError has
                # violated its contract. Treat as non-retryable and fail over:
                # never retry blind, since an unclassified error may be a 400 in
                # disguise and retrying it just burns free-tier quota.
                breaker.record_failure()
                msg = f"{spec.name} raised an unexpected error: {exc}"
                raise ProviderError(msg, provider=spec.name, retryable=False) from exc
            else:
                latency_ms = (self._clock() - started) * 1000.0
                breaker.record_success()
                return result, latency_ms, attempts

        # Unreachable: the loop either returns or raises. Kept explicit so a
        # future edit to the range cannot silently fall through to None.
        if last_error is not None:
            raise last_error
        msg = f"{spec.name} exhausted with no recorded error"
        raise ProviderError(msg, provider=spec.name, retryable=False)

    async def acomplete(self, request: CompletionRequest) -> CompletionResponse:
        """Complete ``request``, walking the fallback chain until one succeeds.

        Raises:
            BudgetExceededError: The virtual key's ceiling is already reached.
            AllProvidersFailedError: Every provider failed; carries each cause.
        """
        cached = self._cache_get(request.idempotency_key)
        if cached is not None:
            log.debug("idempotent_replay", key=request.idempotency_key)
            return cached

        if self._budget is not None:
            await self._budget.check()

        failures: dict[str, str] = {}
        total_attempts = 0

        for spec in self._chain:
            try:
                result, latency_ms, attempts = await self._attempt_provider(spec, request)
            except ProviderError as exc:
                total_attempts += 1
                failures[spec.name] = str(exc)
                log.warning("provider_failed_over", provider=spec.name, cause=str(exc))
                continue

            total_attempts += attempts
            cost = spec.price.cost_for(result.usage)
            if self._budget is not None:
                await self._budget.record(cost)

            response = CompletionResponse(
                text=result.text,
                provider=spec.name,
                model=result.model,
                usage=result.usage,
                cost_usd=cost,
                latency_ms=latency_ms,
                finish_reason=result.finish_reason,
                cache_hit=result.cache_hit,
                attempts=total_attempts,
                fallback_path=tuple(failures.keys()),
            )
            self._cache_put(request.idempotency_key, response)
            return response

        raise AllProvidersFailedError(failures)

    async def acomplete_model(
        self,
        request: CompletionRequest,
        response_model: type[TModel],
    ) -> tuple[TModel, CompletionResponse]:
        """Complete ``request`` and validate the body against ``response_model``.

        The schema is sent to the provider and the response is parsed by Pydantic.
        There is deliberately no regex extraction and no "repair" pass: a body
        that does not validate is an error worth surfacing, not one to paper over.

        Returns:
            The validated model and the raw annotated response (for cost/latency).

        Raises:
            StructuredOutputError: The body was not valid JSON for the schema.
        """
        schema_request = request.model_copy(
            update={"json_schema": response_model.model_json_schema()}
        )
        response = await self.acomplete(schema_request)
        try:
            parsed = response_model.model_validate_json(response.text)
        except ValidationError as exc:
            msg = f"{response.provider} returned a body failing {response_model.__name__}: {exc}"
            raise StructuredOutputError(msg, raw=response.text) from exc
        return parsed, response
