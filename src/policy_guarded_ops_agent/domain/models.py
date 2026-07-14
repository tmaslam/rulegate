"""Billing/subscription entities.

Money
-----
Every monetary amount is a :class:`~decimal.Decimal` constrained to two decimal
places, never a ``float``. ``0.1 + 0.2 != 0.3`` in binary floating point, and a
refund engine that is a cent out is a refund engine nobody will deploy. The
``USD`` alias below is the only monetary type in this codebase.

Time
----
Every timestamp is timezone-aware UTC. Naive datetimes are rejected at the
boundary by :func:`ensure_utc`, because "no refund after 30 days" is a question
about *instants*, and comparing a naive local time to a UTC instant silently
gives the wrong answer for anyone not on UTC. ruff's ``DTZ`` ruleset enforces
the same thing at lint time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "PLAN_MONTHLY_USD",
    "USD",
    "Charge",
    "Customer",
    "CustomerTier",
    "PlanTier",
    "Refund",
    "Subscription",
    "SubscriptionStatus",
    "ensure_utc",
    "plan_rank",
]

#: The only monetary type in this codebase. Two decimal places, never a float.
#: `max_digits=12` comfortably exceeds any plausible SaaS invoice while still
#: rejecting a runaway value.
USD = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as timezone-aware UTC, rejecting naive datetimes.

    A naive datetime is genuinely ambiguous — there is no correct way to read it
    without knowing the writer's zone. Guessing UTC is how "30 days" becomes
    "30 days ± your server's offset". Rejecting is the only honest option.

    Raises:
        ValueError: ``value`` carries no timezone.
    """
    if value.tzinfo is None:
        msg = (
            "naive datetime rejected: every instant in this system must be "
            "timezone-aware UTC, because policy windows are computed from it"
        )
        raise ValueError(msg)
    return value.astimezone(UTC)


class PlanTier(StrEnum):
    """Subscription plan tiers, ordered by :func:`plan_rank`."""

    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


#: Ordering used to classify a plan change as an upgrade or a downgrade.
#: Explicit rather than relying on enum declaration order, which is invisible at
#: the call site and silently reorderable.
_PLAN_RANK: Final[dict[PlanTier, int]] = {
    PlanTier.FREE: 0,
    PlanTier.BASIC: 1,
    PlanTier.PRO: 2,
    PlanTier.ENTERPRISE: 3,
}

#: List price per month. A fact about the mock price list, not a measurement.
PLAN_MONTHLY_USD: Final[dict[PlanTier, Decimal]] = {
    PlanTier.FREE: Decimal("0.00"),
    PlanTier.BASIC: Decimal("29.00"),
    PlanTier.PRO: Decimal("99.00"),
    PlanTier.ENTERPRISE: Decimal("899.00"),
}


def plan_rank(plan: PlanTier) -> int:
    """Rank of ``plan``. Higher is a richer plan; a drop in rank is a downgrade."""
    return _PLAN_RANK[plan]


class CustomerTier(StrEnum):
    """Support tier. Affects routing only — never policy outcomes."""

    STANDARD = "standard"
    PRIORITY = "priority"


class SubscriptionStatus(StrEnum):
    """Lifecycle state of a subscription."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class _Entity(BaseModel):
    """Shared config: frozen, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Customer(_Entity):
    """A billing customer."""

    id: str
    email: str
    name: str
    tier: CustomerTier = CustomerTier.STANDARD
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class Subscription(_Entity):
    """A customer's subscription and its current billing period.

    ``current_period_start``/``current_period_end`` define the cycle that the
    mid-cycle downgrade rule reasons about.
    """

    id: str
    customer_id: str
    plan: PlanTier
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime

    @field_validator("current_period_start", "current_period_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @property
    def monthly_price_usd(self) -> Decimal:
        """List price of the current plan."""
        return PLAN_MONTHLY_USD[self.plan]

    def is_mid_cycle(self, now: datetime) -> bool:
        """Whether ``now`` falls strictly inside the current billing period.

        Strict on both ends on purpose. Exactly at a period boundary the change
        is a renewal-time change, not a mid-cycle one, and the proration rule
        must not fire on it.
        """
        moment = ensure_utc(now)
        return self.current_period_start < moment < self.current_period_end


class Charge(_Entity):
    """A settled charge against a customer. The unit a refund refers to."""

    id: str
    customer_id: str
    subscription_id: str | None
    amount_usd: USD
    #: Sum of refunds already issued against this charge. Never exceeds
    #: ``amount_usd`` — enforced by the billing API, re-checked by policy.
    refunded_usd: USD = Decimal("0.00")
    charged_at: datetime
    description: str = ""

    @field_validator("charged_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @property
    def refundable_usd(self) -> Decimal:
        """Amount still refundable on this charge."""
        return self.amount_usd - self.refunded_usd

    def age_days(self, now: datetime) -> float:
        """Age of the charge in days at ``now``.

        Fractional on purpose: the 30-day refund window is an instant-to-instant
        comparison, and rounding to whole days would hand out a refund up to
        24 hours after the window closed.
        """
        return (ensure_utc(now) - self.charged_at).total_seconds() / 86_400.0


class Refund(_Entity):
    """A refund issued against a :class:`Charge`."""

    id: str
    charge_id: str
    customer_id: str
    amount_usd: USD
    reason: str
    created_at: datetime
    #: The key that produced this refund, when one was supplied. Replaying the
    #: same key returns this same record instead of moving money twice.
    idempotency_key: str | None = None
    #: Id of the human who approved it, when the action required approval.
    approved_by: str | None = None

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
