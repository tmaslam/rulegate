"""Gateway tests. All offline, all deterministic, no API key.

Every resilience behaviour is asserted against injected time — the retry tests
never actually sleep, so the full backoff schedule is verified in microseconds.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from policy_guarded_ops_agent.fakes.fake_llm import (
    FakeLLMBackend,
    FakeRule,
    ScriptedFailure,
    fake_provider_spec,
    user_request,
)
from policy_guarded_ops_agent.llm.gateway import (
    AllProvidersFailedError,
    BudgetExceededError,
    BudgetLedger,
    ChatMessage,
    CircuitBreaker,
    CircuitState,
    CompletionRequest,
    Gateway,
    PriceSpec,
    ProviderError,
    ProviderSpec,
    TokenBucket,
    Usage,
    build_default_chain,
)


class TestCompletion:
    async def test_returns_rule_response(self, gateway: Gateway) -> None:
        response = await gateway.acomplete(user_request("What is the capital of France?"))
        assert response.text == "Paris"
        assert response.provider == "fake"
        assert response.attempts == 1

    async def test_unmatched_prompt_is_self_identifying(self, gateway: Gateway) -> None:
        # An unmatched prompt must fail assertions loudly, never look like a
        # plausible answer that passes by luck.
        response = await gateway.acomplete(user_request("something with no rule"))
        assert response.text.startswith("[fake:no-rule-matched:")

    async def test_fake_is_deterministic_across_instances(self, spec: ProviderSpec) -> None:
        # Determinism is the whole contract of the fake. hash() would break this
        # across processes because it is PYTHONHASHSEED-salted.
        first = await FakeLLMBackend().acomplete(spec, user_request("abc"), timeout_s=30)
        second = await FakeLLMBackend().acomplete(spec, user_request("abc"), timeout_s=30)
        assert first.text == second.text

    async def test_usage_and_cost_are_populated(self, gateway: Gateway) -> None:
        response = await gateway.acomplete(user_request("ping"))
        assert response.usage.prompt_tokens > 0
        assert response.usage.completion_tokens > 0
        # Free tier: genuinely $0, a fact about the price list.
        assert response.cost_usd == Decimal(0)


class TestRetry:
    async def test_backoff_schedule_is_1_2_4_8(self, spec: ProviderSpec, instant_sleep) -> None:
        slept, sleeper = instant_sleep
        backend = FakeLLMBackend(
            rules=[FakeRule(contains="ping", response="pong")],
            failures=[ScriptedFailure(on_call=i, status_code=429) for i in range(1, 5)],
        )
        gateway = Gateway(
            chain=[spec],
            backend=backend,
            sleeper=sleeper,
            jitter=lambda d: d,  # disable jitter so the schedule is exact
        )
        response = await gateway.acomplete(user_request("ping"))
        assert response.text == "pong"
        assert slept == [1.0, 2.0, 4.0, 8.0]
        assert response.attempts == 5

    async def test_retries_on_5xx(self, spec: ProviderSpec, instant_sleep) -> None:
        _, sleeper = instant_sleep
        backend = FakeLLMBackend(
            rules=[FakeRule(contains="ping", response="pong")],
            failures=[ScriptedFailure(on_call=1, status_code=503)],
        )
        gateway = Gateway(chain=[spec], backend=backend, sleeper=sleeper, jitter=lambda d: d)
        assert (await gateway.acomplete(user_request("ping"))).text == "pong"

    async def test_does_not_retry_4xx(self, spec: ProviderSpec, instant_sleep) -> None:
        slept, sleeper = instant_sleep
        backend = FakeLLMBackend(
            rules=[FakeRule(contains="ping", response="pong")],
            failures=[ScriptedFailure(on_call=1, status_code=400, retryable=False)],
        )
        gateway = Gateway(chain=[spec], backend=backend, sleeper=sleeper, jitter=lambda d: d)
        with pytest.raises(AllProvidersFailedError):
            await gateway.acomplete(user_request("ping"))
        # A 400 is our bug. Retrying it four times just burns free-tier quota.
        assert slept == []
        assert backend.call_count == 1

    async def test_jitter_is_applied_within_bounds(self, spec: ProviderSpec, instant_sleep) -> None:
        slept, sleeper = instant_sleep
        backend = FakeLLMBackend(
            rules=[FakeRule(contains="ping", response="pong")],
            failures=[ScriptedFailure(on_call=1, status_code=429)],
        )
        gateway = Gateway(chain=[spec], backend=backend, sleeper=sleeper)
        await gateway.acomplete(user_request("ping"))
        # Full jitter: uniform in [0, delay].
        assert len(slept) == 1
        assert 0.0 <= slept[0] <= 1.0


class TestFallback:
    async def test_falls_over_to_next_provider(self, instant_sleep):
        _, sleeper = instant_sleep
        alpha, beta = fake_provider_spec("alpha"), fake_provider_spec("beta")

        class AlphaAlwaysFails:
            def __init__(self) -> None:
                self.inner = FakeLLMBackend(rules=[FakeRule(contains="hi", response="from-beta")])
                self.seen: list[str] = []

            async def acomplete(self, spec, request, *, timeout_s):
                self.seen.append(spec.name)
                if spec.name == "alpha":
                    msg = "alpha is down"
                    raise ProviderError(msg, provider="alpha", status_code=500, retryable=False)
                return await self.inner.acomplete(spec, request, timeout_s=timeout_s)

        backend = AlphaAlwaysFails()
        gateway = Gateway(chain=[alpha, beta], backend=backend, sleeper=sleeper, jitter=lambda d: d)
        response = await gateway.acomplete(user_request("hi"))
        assert response.text == "from-beta"
        assert response.provider == "beta"
        assert response.fallback_path == ("alpha",)

    async def test_exhausted_chain_reports_every_cause(self, spec: ProviderSpec, instant_sleep) -> None:
        _, sleeper = instant_sleep
        backend = FakeLLMBackend(
            failures=[ScriptedFailure(on_call=i, status_code=500) for i in range(1, 30)]
        )
        gateway = Gateway(chain=[spec], backend=backend, sleeper=sleeper, jitter=lambda d: d)
        with pytest.raises(AllProvidersFailedError) as exc_info:
            await gateway.acomplete(user_request("x"))
        assert "fake" in exc_info.value.failures

    def test_empty_chain_is_rejected_with_a_useful_message(self) -> None:
        with pytest.raises(ValueError, match="fake_provider_spec"):
            Gateway(chain=[])


class TestCircuitBreaker:
    def test_opens_after_threshold(self) -> None:
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_s=30, clock=lambda: now[0])
        assert breaker.state is CircuitState.CLOSED
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state is CircuitState.OPEN
        assert not breaker.allow()

    def test_half_opens_after_recovery_and_admits_one_probe(self) -> None:
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_s=30, clock=lambda: now[0])
        breaker.record_failure()
        now[0] = 31.0
        assert breaker.state is CircuitState.HALF_OPEN
        assert breaker.allow()      # first probe admitted
        assert not breaker.allow()  # second is not — only one probe at a time

    def test_success_closes_the_circuit(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_s=30)
        breaker.record_failure()
        assert breaker.state is CircuitState.OPEN
        breaker.record_success()
        assert breaker.state is CircuitState.CLOSED

    async def test_gateway_opens_circuit_on_repeated_failure(
        self, spec: ProviderSpec, instant_sleep
    ):
        _, sleeper = instant_sleep
        now = [0.0]
        backend = FakeLLMBackend(
            failures=[ScriptedFailure(on_call=i, status_code=500) for i in range(1, 60)]
        )
        gateway = Gateway(
            chain=[spec],
            backend=backend,
            sleeper=sleeper,
            jitter=lambda d: d,
            clock=lambda: now[0],
            failure_threshold=3,
        )
        for _ in range(2):
            with pytest.raises(AllProvidersFailedError):
                await gateway.acomplete(user_request("x"))
        assert gateway.circuit_state("fake") is CircuitState.OPEN


class TestRateLimiter:
    async def test_bucket_allows_burst_then_throttles(self, instant_sleep) -> None:
        slept, sleeper = instant_sleep
        now = [0.0]
        bucket = TokenBucket(rate_per_minute=60, burst=2, clock=lambda: now[0], sleeper=sleeper)
        assert await bucket.acquire() == 0.0
        assert await bucket.acquire() == 0.0
        # Burst is spent; the third must wait ~1s at 60rpm.
        waited = await bucket.acquire()
        assert waited > 0.0
        assert slept

    def test_rejects_nonsense_rate(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            TokenBucket(rate_per_minute=0, burst=1)


class TestBudget:
    async def test_unknown_price_is_not_fabricated_as_zero(self, instant_sleep):
        # A provider with no price list must yield cost None and be counted as
        # unpriced, never silently recorded as $0.
        _, _ = instant_sleep
        unpriced = ProviderSpec(name="unpriced", model="x/y", api_key_env=None, rpm=1000, burst=50)
        ledger = BudgetLedger(limit_usd=Decimal("1.00"))
        gateway = Gateway(
            chain=[unpriced],
            backend=FakeLLMBackend(rules=[FakeRule(contains="q", response="a")]),
            budget=ledger,
        )
        response = await gateway.acomplete(user_request("q"))
        assert response.cost_usd is None
        assert ledger.unpriced_calls == 1
        assert ledger.spent_usd == Decimal(0)

    async def test_exceeding_budget_refuses_the_request(self) -> None:
        ledger = BudgetLedger(limit_usd=Decimal("0.01"))
        await ledger.record(Decimal("0.05"))
        gateway = Gateway(
            chain=[fake_provider_spec()],
            backend=FakeLLMBackend(),
            budget=ledger,
        )
        with pytest.raises(BudgetExceededError):
            await gateway.acomplete(user_request("q"))

    def test_price_spec_subtracts_cached_tokens_from_billable_input(self) -> None:
        price = PriceSpec(usd_per_1m_input=Decimal(1), usd_per_1m_output=Decimal(2))
        usage = Usage(prompt_tokens=1_000_000, completion_tokens=0, cached_prompt_tokens=400_000)
        # Only 600k tokens are billable.
        assert price.cost_for(usage) == Decimal("0.6")

    def test_free_tier_is_exactly_zero(self) -> None:
        price = PriceSpec(is_free_tier=True)
        assert price.cost_for(Usage(prompt_tokens=10**9, completion_tokens=10**9)) == Decimal(0)

    def test_unknown_price_returns_none(self) -> None:
        assert PriceSpec().cost_for(Usage(prompt_tokens=100)) is None


class TestIdempotency:
    async def test_same_key_is_served_once(self, spec: ProviderSpec) -> None:
        backend = FakeLLMBackend(rules=[FakeRule(contains="once", response="v1")])
        gateway = Gateway(chain=[spec], backend=backend)
        first = await gateway.acomplete(user_request("once", idempotency_key="k"))
        second = await gateway.acomplete(user_request("once", idempotency_key="k"))
        assert first.text == second.text
        assert backend.call_count == 1

    async def test_no_key_means_no_dedupe(self, spec: ProviderSpec) -> None:
        backend = FakeLLMBackend(rules=[FakeRule(contains="once", response="v1")])
        gateway = Gateway(chain=[spec], backend=backend)
        await gateway.acomplete(user_request("once"))
        await gateway.acomplete(user_request("once"))
        assert backend.call_count == 2


class TestPromptCaching:
    async def test_cache_hit_is_reported(self, spec: ProviderSpec) -> None:
        backend = FakeLLMBackend(
            rules=[FakeRule(contains="cached", response="hit", cache_hit=True)]
        )
        gateway = Gateway(chain=[spec], backend=backend)
        response = await gateway.acomplete(
            CompletionRequest(
                messages=(ChatMessage(role="user", content="cached", cacheable=True),)
            )
        )
        assert response.cache_hit
        assert response.usage.cached_prompt_tokens > 0


class TestTimeout:
    async def test_simulated_latency_over_budget_raises_retryable(self, spec: ProviderSpec) -> None:
        backend = FakeLLMBackend(rules=[FakeRule(contains="slow", response="x", latency_ms=60_000)])
        with pytest.raises(ProviderError) as exc_info:
            await backend.acomplete(spec, user_request("slow"), timeout_s=1.0)
        assert exc_info.value.retryable


class TestDefaultChain:
    def test_no_keys_yields_empty_chain(self) -> None:
        # The supported zero-account state: caller falls back to the fake.
        assert build_default_chain(env={}) == ()

    def test_only_configured_providers_are_included(self) -> None:
        chain = build_default_chain(env={"GROQ_API_KEY": "x"})
        assert [s.name for s in chain] == ["groq"]

    def test_explicit_chain_order_is_honoured(self) -> None:
        chain = build_default_chain(
            env={
                "LLM_FALLBACK_CHAIN": "gemini,groq",
                "GROQ_API_KEY": "x",
                "GEMINI_API_KEY": "y",
            }
        )
        assert [s.name for s in chain] == ["gemini", "groq"]

    def test_unknown_provider_name_is_skipped_not_fatal(self) -> None:
        chain = build_default_chain(
            env={"LLM_FALLBACK_CHAIN": "nonsense,groq", "GROQ_API_KEY": "x"}
        )
        assert [s.name for s in chain] == ["groq"]

    def test_blank_key_does_not_count_as_configured(self) -> None:
        assert build_default_chain(env={"GROQ_API_KEY": "   "}) == ()

    def test_openrouter_default_model_is_a_free_variant(self) -> None:
        # The `:free` suffix is what keeps OpenRouter zero-cost. If this ever
        # regresses, the repo silently starts costing money.
        from policy_guarded_ops_agent.llm.gateway import FREE_TIER_PROVIDERS

        assert FREE_TIER_PROVIDERS["openrouter"].model.endswith(":free")

    def test_every_registry_provider_is_free(self) -> None:
        from policy_guarded_ops_agent.llm.gateway import FREE_TIER_PROVIDERS

        for name, provider in FREE_TIER_PROVIDERS.items():
            assert provider.price.is_free_tier, f"{name} is not marked free"
