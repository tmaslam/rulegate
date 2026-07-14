"""Deterministic fakes. Ship these in the package — the eval harness imports them."""

from __future__ import annotations

from policy_guarded_ops_agent.fakes.fake_llm import (
    FAKE_PROVIDER_NAME,
    FakeLLMBackend,
    FakeRule,
    ScriptedFailure,
    fake_provider_spec,
    user_request,
)

__all__ = [
    "FAKE_PROVIDER_NAME",
    "FakeLLMBackend",
    "FakeRule",
    "ScriptedFailure",
    "fake_provider_spec",
    "user_request",
]
