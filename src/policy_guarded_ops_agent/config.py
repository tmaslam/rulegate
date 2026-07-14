"""Typed settings, resolved from the environment exactly once.

Everything optional
-------------------
Every field has a default that works with **no ``.env``, no keys and no
accounts**. That is not a convenience — it is the repo's third non-negotiable.
A fresh clone runs: no ``DATABASE_URL`` selects SQLite, no provider key selects
the deterministic fake, no ``LANGFUSE_*`` makes tracing a no-op.

Validation happens here, at the boundary, so a typo in ``.env`` fails at startup
with a field name rather than at 3am with a ``TypeError`` deep in a rule.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from policy_guarded_ops_agent.policy.models import (
    DEFAULT_ESCALATION_THRESHOLD_USD,
    DEFAULT_REFUND_WINDOW_DAYS,
)

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Service configuration.

    Field names map to upper-case env vars (``policy_enabled`` -> ``POLICY_ENABLED``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore unknown vars: .env is shared with the provider keys the gateway
        # reads directly, and `extra="forbid"` would make a documented key a
        # startup crash.
        extra="ignore",
        frozen=True,
    )

    # -- service ------------------------------------------------------------
    environment: Literal["local", "ci", "staging", "production"] = "local"
    service_name: str = "policy-guarded-ops-agent"

    # -- storage ------------------------------------------------------------
    #: Neon/Postgres URL. Unset => SQLite fallback. Both paths are supported.
    database_url: str | None = None

    # -- the ablation flag --------------------------------------------------
    #: THE headline flag. False bypasses the policy engine entirely so the
    #: violation rate can be measured with the guard off. The violation
    #: detector still runs, which is what makes the two arms comparable.
    #:
    #: **Never set this False in production.** It exists to measure what the
    #: guard is worth, and the service logs a warning on every bypassed action.
    policy_enabled: bool = True

    # -- policy tunables ----------------------------------------------------
    escalation_threshold_usd: Decimal = Field(
        default=DEFAULT_ESCALATION_THRESHOLD_USD,
        gt=Decimal(0),
        description="Actions worth strictly more than this require human approval.",
    )
    refund_window_days: int = Field(
        default=DEFAULT_REFUND_WINDOW_DAYS,
        gt=0,
        description="No refund more than this many days after the charge.",
    )

    # -- llm ----------------------------------------------------------------
    #: Comma-separated provider order. Empty => gateway's default free-tier
    #: chain, which itself degrades to the fake when no key is present.
    llm_fallback_chain: str = ""
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    #: Seed is a hint, never a determinism guarantee — report it with any number.
    llm_seed: int | None = 0
    llm_timeout_s: float = Field(default=30.0, gt=0)

    # -- guardrails ---------------------------------------------------------
    max_input_chars: int = Field(default=8_000, gt=0)

    @field_validator("database_url")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """Treat an empty ``DATABASE_URL=`` as unset.

        ``.env.example`` ships keys with empty values, and `DATABASE_URL=` would
        otherwise resolve to `""` — which is neither a valid URL nor `None`, and
        would fail the scheme check instead of quietly selecting SQLite.
        """
        if value is None or not value.strip():
            return None
        return value.strip()


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment once.

    Cached because ``.env`` parsing is I/O and settings are immutable for the
    process's lifetime. Tests construct ``Settings(...)`` directly instead of
    going through here, so the cache never leaks between them.
    """
    global _cached  # noqa: PLW0603 — process-wide config is a singleton by design.
    if _cached is None:
        _cached = Settings()
    return _cached


#: Convenience for modules that want the defaults without an env read.
DEFAULT_SETTINGS: Final = Settings
