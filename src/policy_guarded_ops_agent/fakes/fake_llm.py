"""Deterministic fake LLM backend — the reason this repo's CI is free.

Why this exists
---------------
Every test and every eval in this repo runs green offline, with no API key and
no network, because the gateway takes an injected :class:`CompletionBackend` and
this module supplies a deterministic one. GitHub Actions on a public repo is
free; a CI run that called a real provider would be neither free nor
reproducible, and a rate-limited free tier would make it flaky on top.

What it is NOT
--------------
**This fake produces no evidence about model quality.** An eval run against it
measures the *scaffold* — routing, parsing, guardrails, assertion logic — and
nothing else. A score from a fake-backed run must never be reported as a model
result. :class:`~evals.harness.EvalReport` records the backend identity for
exactly this reason, and its Markdown renderer labels such runs as
scaffold-only. If you want a model number, run against a real provider and
report it with model+version, temperature, seed, cost and latency attached.

Determinism guarantees
----------------------
Same input ⇒ same output, forever, on every machine and Python build:

* Rules are matched in registration order (first match wins).
* The fallback response is derived from a BLAKE2b digest of the request. Python's
  ``hash()`` is salted per-process and is deliberately not used.
* Latency is simulated arithmetically, never slept, so the suite stays fast.

Copy to ``src/<package>/fakes/fake_llm.py`` (ship it in the package, not just in
tests — the eval harness imports it).
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from policy_guarded_ops_agent.llm.gateway import (
    BackendResult,
    ChatMessage,
    CompletionRequest,
    PriceSpec,
    ProviderError,
    ProviderSpec,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "FAKE_PROVIDER_NAME",
    "FakeLLMBackend",
    "FakeRule",
    "ScriptedFailure",
    "fake_provider_spec",
    "user_request",
]

#: Provider name used by the fake. Reports key off this string to mark a run as
#: scaffold-only, so keep it stable.
FAKE_PROVIDER_NAME: Final = "fake"

_DIGEST_BYTES: Final = 8
#: Rough token estimate for simulated usage. Deliberately crude: it is a
#: plausible-shaped integer for exercising accounting code paths, and is never a
#: real tokenizer count. Nothing derived from it may be reported as a token
#: measurement.
_CHARS_PER_TOKEN: Final = 4


class FakeRule(BaseModel):
    """Map a request to a canned response.

    Exactly one matcher must be set. ``pattern`` is a regex applied to the
    concatenated message contents; ``contains`` is a plain substring check.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    response: str
    contains: str | None = None
    pattern: str | None = None
    #: Simulated latency, in milliseconds. Never slept — the suite stays fast.
    #: Used only to exercise the gateway's timeout path: a rule whose latency
    #: exceeds the request timeout raises a retryable ProviderError. It is NOT
    #: the latency the gateway reports; the gateway measures its own wall-clock,
    #: which for an in-process fake is ~0.
    latency_ms: float = Field(default=5.0, ge=0.0)
    finish_reason: str = "stop"
    #: Emulate a prompt-cache hit for testing the cached-token accounting path.
    cache_hit: bool = False

    def matches(self, prompt: str) -> bool:
        """Whether this rule applies to ``prompt``."""
        if self.contains is not None:
            return self.contains in prompt
        if self.pattern is not None:
            return re.search(self.pattern, prompt) is not None
        return False

    @model_validator(mode="after")
    def _exactly_one_matcher(self) -> Self:
        """Reject rules with zero or two matchers — a silent no-match is worse."""
        if (self.contains is None) == (self.pattern is None):
            msg = "FakeRule requires exactly one of `contains` or `pattern`"
            raise ValueError(msg)
        if self.pattern is not None:
            # Fail at construction, not at first match, on a bad regex.
            re.compile(self.pattern)
        return self


class ScriptedFailure(BaseModel):
    """Force a failure on the Nth call, to exercise retry/fallback/breaker paths.

    ``on_call`` is 1-indexed over this backend instance's lifetime. A backend
    scripted with ``ScriptedFailure(on_call=1, status_code=429)`` lets a test
    assert that the gateway backs off and retries rather than failing over.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_call: int = Field(ge=1)
    status_code: int = 429
    retryable: bool = True
    message: str = "scripted failure"


class FakeLLMBackend:
    """A :class:`~llm.gateway.CompletionBackend` that never touches the network.

    Example::

        backend = FakeLLMBackend(rules=[FakeRule(contains="capital of France", response="Paris")])
        gateway = Gateway(chain=[fake_provider_spec()], backend=backend)

    Any prompt not matched by a rule receives a stable digest-derived string, so
    unmatched cases fail assertions loudly and visibly instead of accidentally
    passing on a lucky guess.
    """

    def __init__(
        self,
        *,
        rules: Sequence[FakeRule] = (),
        failures: Iterable[ScriptedFailure] = (),
        default_response: str | Callable[[str], str] | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._failures = {f.on_call: f for f in failures}
        self._default = default_response
        self._calls = 0
        self._prompts: list[str] = []

    @property
    def call_count(self) -> int:
        """Attempts made against this backend, including ones scripted to fail."""
        return self._calls

    @property
    def prompts(self) -> tuple[str, ...]:
        """Every prompt seen, in order. For asserting on what the scaffold sent."""
        return tuple(self._prompts)

    def reset(self) -> None:
        """Clear call history and the scripted-failure cursor."""
        self._calls = 0
        self._prompts.clear()

    @staticmethod
    def _flatten(request: CompletionRequest) -> str:
        """Collapse messages into one string for matching and hashing."""
        return "\n".join(f"{m.role}: {m.content}" for m in request.messages)

    @staticmethod
    def _digest(prompt: str) -> str:
        """Stable short digest. BLAKE2b, not hash() — hash() is PYTHONHASHSEED-salted."""
        return hashlib.blake2b(prompt.encode("utf-8"), digest_size=_DIGEST_BYTES).hexdigest()

    def _fallback_text(self, prompt: str) -> str:
        if callable(self._default):
            return self._default(prompt)
        if isinstance(self._default, str):
            return self._default
        # Self-identifying so an unmatched prompt is obvious in a failing diff.
        return f"[fake:no-rule-matched:{self._digest(prompt)}]"

    async def acomplete(
        self,
        spec: ProviderSpec,
        request: CompletionRequest,
        *,
        timeout_s: float,
    ) -> BackendResult:
        """Return a deterministic result. Never sleeps, never opens a socket.

        Raises:
            ProviderError: When a :class:`ScriptedFailure` targets this call, or
                when a rule's simulated latency exceeds ``timeout_s`` — which
                makes the gateway's timeout path testable without real waiting.
        """
        self._calls += 1
        prompt = self._flatten(request)
        self._prompts.append(prompt)

        scripted = self._failures.get(self._calls)
        if scripted is not None:
            raise ProviderError(
                scripted.message,
                provider=spec.name,
                status_code=scripted.status_code,
                retryable=scripted.retryable,
            )

        rule = next((r for r in self._rules if r.matches(prompt)), None)
        text = rule.response if rule is not None else self._fallback_text(prompt)
        latency_ms = rule.latency_ms if rule is not None else 5.0

        if latency_ms / 1000.0 > timeout_s:
            msg = f"{spec.name} simulated timeout ({latency_ms}ms > {timeout_s}s)"
            raise ProviderError(msg, provider=spec.name, retryable=True)

        cached = 0
        prompt_tokens = max(len(prompt) // _CHARS_PER_TOKEN, 1)
        if rule is not None and rule.cache_hit:
            cached = prompt_tokens // 2

        return BackendResult(
            text=text,
            model=spec.model,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=max(len(text) // _CHARS_PER_TOKEN, 1),
                cached_prompt_tokens=cached,
            ),
            finish_reason=rule.finish_reason if rule is not None else "stop",
            cache_hit=cached > 0,
        )


def fake_provider_spec(name: str = FAKE_PROVIDER_NAME) -> ProviderSpec:
    """Build a :class:`ProviderSpec` pointing at the fake.

    Keyless (``api_key_env=None``) so it is always available, with a high RPM so
    the token bucket never throttles a test suite. Priced ``is_free_tier=True``
    because it is free in the most literal sense — it is not a provider.
    """
    return ProviderSpec(
        name=name,
        model=f"{name}/deterministic-v1",
        api_key_env=None,
        rpm=100_000,
        burst=1_000,
        supports_prompt_cache=True,
        price=PriceSpec(is_free_tier=True),
    )


def user_request(
    content: str,
    *,
    system: str | None = None,
    temperature: float = 0.0,
    seed: int | None = 0,
    idempotency_key: str | None = None,
) -> CompletionRequest:
    """Shorthand for a one-shot request. Convenience for tests and the harness."""
    messages: list[ChatMessage] = []
    if system is not None:
        messages.append(ChatMessage(role="system", content=system))
    messages.append(ChatMessage(role="user", content=content))
    return CompletionRequest(
        messages=tuple(messages),
        temperature=temperature,
        seed=seed,
        idempotency_key=idempotency_key,
    )
