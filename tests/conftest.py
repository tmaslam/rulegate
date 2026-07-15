"""Shared fixtures. Every fixture here is offline and key-free by construction."""

from __future__ import annotations

from decimal import Decimal

import pytest

from policy_guarded_ops_agent.fakes.fake_llm import FakeLLMBackend, FakeRule, fake_provider_spec
from policy_guarded_ops_agent.llm.gateway import BudgetLedger, Gateway, ProviderSpec, Sleeper


@pytest.fixture
def spec() -> ProviderSpec:
    """A keyless fake provider spec."""
    return fake_provider_spec()


@pytest.fixture
def backend() -> FakeLLMBackend:
    """A fake backend with a couple of deterministic rules."""
    return FakeLLMBackend(
        rules=[
            FakeRule(contains="capital of France", response="Paris"),
            FakeRule(contains="ping", response="pong"),
        ]
    )


@pytest.fixture
def instant_sleep() -> tuple[list[float], Sleeper]:
    """A sleeper that records delays instead of waiting.

    Returns the recording list and the coroutine function. Keeps retry tests at
    microseconds instead of the 15s the real backoff schedule would cost.
    """
    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    return slept, _sleep


@pytest.fixture
def gateway(spec: ProviderSpec, backend: FakeLLMBackend) -> Gateway:
    """A gateway wired to the fake. No key, no network."""
    return Gateway(chain=[spec], backend=backend)


@pytest.fixture
def ledger() -> BudgetLedger:
    """A zero-limit budget ledger, matching the free-tier default."""
    return BudgetLedger(limit_usd=Decimal("0.00"))


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test can accidentally reach a real provider.

    Autouse and non-negotiable: if a developer has GROQ_API_KEY exported in their
    shell, a test that builds the default chain would otherwise make a real,
    rate-limited, non-deterministic network call — and it would pass locally and
    fail in CI. Stripping the keys here makes that impossible.
    """
    for var in (
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "CEREBRAS_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
