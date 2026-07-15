"""In-memory mock billing API with deterministic seed data.

Why a mock
----------
The brief is explicit that a real payment processor is out of scope. Refunds are
irreversible and cost real money, so a portfolio demo must not touch one. What
this mock *does* preserve is the shape of the problem: async I/O, entities that
can be missing, operations that fail, and — crucially — **idempotency**, because
"the retry issued the refund twice" is the defining failure mode of this domain.

Determinism
-----------
Seed data is generated at fixed offsets from an injected clock, so "this charge
is 45 days old" is true whenever you run it and identical on every machine.
There is no ``datetime.now()`` reached for anywhere in this module; the clock is
a constructor argument, which is what lets tests pin time without freezing the
world (see ``tests/conftest.py::fixed_clock``).

Integrity vs. policy
--------------------
The errors raised here are **system** invariants — over-refunding a charge is
data corruption, not a business-rule decision. Business rules live in
``policy/`` and are enforced *before* anything here is called. Both check the
refundable balance, on purpose: policy so it can explain the refusal to a human
in terms of a named rule, and this module so a bug that bypasses policy still
cannot corrupt the ledger.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog

from policy_guarded_ops_agent.domain.models import (
    Charge,
    Customer,
    CustomerTier,
    PlanTier,
    Refund,
    Subscription,
    SubscriptionStatus,
    ensure_utc,
)

__all__ = [
    "BillingError",
    "ChargeNotFoundError",
    "CustomerNotFoundError",
    "InvalidBillingOperationError",
    "MockBillingAPI",
    "NotFoundError",
    "SubscriptionNotFoundError",
    "utc_now",
]

log: Final = structlog.get_logger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Current instant, timezone-aware UTC. The default clock."""
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BillingError(Exception):
    """Base class for every error raised by the billing API."""


class NotFoundError(BillingError):
    """A referenced entity does not exist.

    Distinct from a policy refusal: "that charge does not exist" is not a
    business decision, and must never be reported to a user as though a rule
    rejected their request.
    """

    def __init__(self, kind: str, entity_id: str) -> None:
        self.kind = kind
        self.entity_id = entity_id
        super().__init__(f"{kind} not found: {entity_id}")


class CustomerNotFoundError(NotFoundError):
    """No such customer."""

    def __init__(self, entity_id: str) -> None:
        super().__init__("customer", entity_id)


class SubscriptionNotFoundError(NotFoundError):
    """No such subscription."""

    def __init__(self, entity_id: str) -> None:
        super().__init__("subscription", entity_id)


class ChargeNotFoundError(NotFoundError):
    """No such charge."""

    def __init__(self, entity_id: str) -> None:
        super().__init__("charge", entity_id)


class InvalidBillingOperationError(BillingError):
    """The operation would corrupt billing state.

    A system-integrity failure, not a policy outcome.
    """


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

#: Fixed offsets (in days) from the clock, chosen so every policy branch has a
#: scenario that exercises it. These are fixtures, not measurements.
_SEED_RECENT_CHARGE_AGE_DAYS: Final = 3
_SEED_STALE_CHARGE_AGE_DAYS: Final = 45
_SEED_LARGE_CHARGE_AGE_DAYS: Final = 5


class _Seed:
    """Deterministic seed data, built relative to ``now``."""

    @staticmethod
    def build(
        now: datetime,
    ) -> tuple[
        dict[str, Customer],
        dict[str, Subscription],
        dict[str, Charge],
    ]:
        """Build the seed graph anchored at ``now``.

        Scenario coverage, by construction:

        * ``chg_recent`` — 3 days old, $99. Inside the refund window, under the
          escalation threshold: the clean approve path.
        * ``chg_stale`` — 45 days old, $99. Outside the 30-day window: exercises
          ``refund-window-30d``.
        * ``chg_large`` — 5 days old, $899. Inside the window but over $500:
          exercises ``high-value-escalation`` and the HITL interrupt.
        * ``sub_pro`` — PRO, mid-cycle: exercises
          ``downgrade-requires-proration``.
        * ``sub_basic`` — BASIC, mid-cycle: a downgrade target and an upgrade
          source.
        * ``sub_canceled`` — already canceled: exercises the integrity guard.
        """
        anchor = ensure_utc(now)
        period_start = anchor - timedelta(days=10)
        period_end = anchor + timedelta(days=20)

        customers = {
            "cus_alice": Customer(
                id="cus_alice",
                email="alice@example.com",
                name="Alice Nguyen",
                tier=CustomerTier.STANDARD,
                created_at=anchor - timedelta(days=400),
            ),
            "cus_bob": Customer(
                id="cus_bob",
                email="bob@example.com",
                name="Bob Idris",
                tier=CustomerTier.PRIORITY,
                created_at=anchor - timedelta(days=120),
            ),
        }

        subscriptions = {
            "sub_pro": Subscription(
                id="sub_pro",
                customer_id="cus_alice",
                plan=PlanTier.PRO,
                status=SubscriptionStatus.ACTIVE,
                current_period_start=period_start,
                current_period_end=period_end,
            ),
            "sub_basic": Subscription(
                id="sub_basic",
                customer_id="cus_bob",
                plan=PlanTier.BASIC,
                status=SubscriptionStatus.ACTIVE,
                current_period_start=period_start,
                current_period_end=period_end,
            ),
            "sub_canceled": Subscription(
                id="sub_canceled",
                customer_id="cus_bob",
                plan=PlanTier.FREE,
                status=SubscriptionStatus.CANCELED,
                current_period_start=period_start,
                current_period_end=period_end,
            ),
        }

        charges = {
            "chg_recent": Charge(
                id="chg_recent",
                customer_id="cus_alice",
                subscription_id="sub_pro",
                amount_usd=Decimal("99.00"),
                charged_at=anchor - timedelta(days=_SEED_RECENT_CHARGE_AGE_DAYS),
                description="Pro plan — current period",
            ),
            "chg_stale": Charge(
                id="chg_stale",
                customer_id="cus_alice",
                subscription_id="sub_pro",
                amount_usd=Decimal("99.00"),
                charged_at=anchor - timedelta(days=_SEED_STALE_CHARGE_AGE_DAYS),
                description="Pro plan — previous period",
            ),
            "chg_large": Charge(
                id="chg_large",
                customer_id="cus_bob",
                subscription_id="sub_basic",
                amount_usd=Decimal("899.00"),
                charged_at=anchor - timedelta(days=_SEED_LARGE_CHARGE_AGE_DAYS),
                description="Enterprise annual — prepaid",
            ),
        }
        return customers, subscriptions, charges


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class MockBillingAPI:
    """A realistic, in-memory billing API.

    Async because the real thing would be, and because the agent graph and the
    FastAPI surface are async end to end. A lock serialises mutations so a
    concurrent double-submit cannot interleave a read-modify-write on a charge.
    """

    def __init__(self, *, clock: Clock = utc_now) -> None:
        self._clock = clock
        customers, subscriptions, charges = _Seed.build(clock())
        self._customers = customers
        self._subscriptions = subscriptions
        self._charges = charges
        self._refunds: dict[str, Refund] = {}
        #: idempotency key -> refund id. The dedupe window for retried writes.
        self._refund_keys: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._refund_seq = 0

    @property
    def clock(self) -> Clock:
        """The injected clock.

        Exposed so collaborators (the tool registry, the graph) read time from
        the *same* source this API stamps records with. Two components reading
        two different clocks is how a refund gets stamped a millisecond before
        the decision that authorised it.
        """
        return self._clock

    def now(self) -> datetime:
        """Current instant per the injected clock."""
        return self._clock()

    # -- reads ---------------------------------------------------------------

    async def get_customer(self, customer_id: str) -> Customer:
        """Fetch a customer.

        Raises:
            CustomerNotFoundError: No such customer.
        """
        customer = self._customers.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError(customer_id)
        return customer

    async def get_subscription(self, subscription_id: str) -> Subscription:
        """Fetch a subscription.

        Raises:
            SubscriptionNotFoundError: No such subscription.
        """
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None:
            raise SubscriptionNotFoundError(subscription_id)
        return subscription

    async def get_charge(self, charge_id: str) -> Charge:
        """Fetch a charge.

        Raises:
            ChargeNotFoundError: No such charge.
        """
        charge = self._charges.get(charge_id)
        if charge is None:
            raise ChargeNotFoundError(charge_id)
        return charge

    async def list_charges(self, customer_id: str) -> tuple[Charge, ...]:
        """Every charge for a customer, newest first."""
        return tuple(
            sorted(
                (c for c in self._charges.values() if c.customer_id == customer_id),
                key=lambda c: c.charged_at,
                reverse=True,
            )
        )

    async def list_subscriptions(self, customer_id: str) -> tuple[Subscription, ...]:
        """Every subscription for a customer."""
        return tuple(s for s in self._subscriptions.values() if s.customer_id == customer_id)

    async def get_refund(self, refund_id: str) -> Refund | None:
        """Fetch a refund by id, or ``None``."""
        return self._refunds.get(refund_id)

    # -- writes --------------------------------------------------------------

    async def issue_refund(
        self,
        *,
        charge_id: str,
        amount_usd: Decimal,
        reason: str,
        idempotency_key: str | None = None,
        approved_by: str | None = None,
    ) -> Refund:
        """Refund ``amount_usd`` against ``charge_id``.

        Idempotent when ``idempotency_key`` is supplied: replaying the same key
        returns the original refund and moves no additional money. This is the
        single most important property in the module — an agent retrying a
        timed-out call is a *normal* event, and without a dedupe key that retry
        is a second real refund.

        Raises:
            ChargeNotFoundError: No such charge.
            InvalidBillingOperationError: The amount is non-positive or exceeds
                the charge's remaining refundable balance.
        """
        async with self._lock:
            if idempotency_key is not None:
                existing_id = self._refund_keys.get(idempotency_key)
                if existing_id is not None:
                    log.info("refund_idempotent_replay", key=idempotency_key, refund_id=existing_id)
                    return self._refunds[existing_id]

            charge = self._charges.get(charge_id)
            if charge is None:
                raise ChargeNotFoundError(charge_id)
            if amount_usd <= Decimal(0):
                msg = f"refund amount must be positive, got {amount_usd}"
                raise InvalidBillingOperationError(msg)
            if amount_usd > charge.refundable_usd:
                # Integrity guard. Policy also checks this and will normally
                # refuse first with a named rule; this is the backstop for a
                # caller that bypassed policy.
                msg = (
                    f"refund of {amount_usd} exceeds refundable balance "
                    f"{charge.refundable_usd} on charge {charge_id}"
                )
                raise InvalidBillingOperationError(msg)

            self._refund_seq += 1
            refund = Refund(
                id=f"re_{self._refund_seq:06d}",
                charge_id=charge_id,
                customer_id=charge.customer_id,
                amount_usd=amount_usd,
                reason=reason,
                created_at=self._clock(),
                idempotency_key=idempotency_key,
                approved_by=approved_by,
            )
            self._charges[charge_id] = charge.model_copy(
                update={"refunded_usd": charge.refunded_usd + amount_usd}
            )
            self._refunds[refund.id] = refund
            if idempotency_key is not None:
                self._refund_keys[idempotency_key] = refund.id
            log.info(
                "refund_issued",
                refund_id=refund.id,
                charge_id=charge_id,
                amount_usd=str(amount_usd),
            )
            return refund

    async def change_plan(
        self,
        *,
        subscription_id: str,
        target_plan: PlanTier,
        prorate: bool = False,
    ) -> Subscription:
        """Move a subscription to ``target_plan``.

        Raises:
            SubscriptionNotFoundError: No such subscription.
            InvalidBillingOperationError: The subscription is canceled, or is
                already on the target plan.
        """
        async with self._lock:
            subscription = self._subscriptions.get(subscription_id)
            if subscription is None:
                raise SubscriptionNotFoundError(subscription_id)
            if subscription.status is SubscriptionStatus.CANCELED:
                msg = f"cannot change the plan of canceled subscription {subscription_id}"
                raise InvalidBillingOperationError(msg)
            if subscription.plan is target_plan:
                msg = f"subscription {subscription_id} is already on plan {target_plan}"
                raise InvalidBillingOperationError(msg)

            updated = subscription.model_copy(update={"plan": target_plan})
            self._subscriptions[subscription_id] = updated
            log.info(
                "plan_changed",
                subscription_id=subscription_id,
                from_plan=str(subscription.plan),
                to_plan=str(target_plan),
                prorated=prorate,
            )
            return updated

    async def cancel_subscription(
        self,
        *,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> Subscription:
        """Cancel a subscription.

        ``at_period_end`` keeps the subscription ACTIVE until the period closes,
        which is what the customer already paid for. Immediate cancellation
        forfeits that time and flips the status now.

        Raises:
            SubscriptionNotFoundError: No such subscription.
            InvalidBillingOperationError: Already canceled.
        """
        async with self._lock:
            subscription = self._subscriptions.get(subscription_id)
            if subscription is None:
                raise SubscriptionNotFoundError(subscription_id)
            if subscription.status is SubscriptionStatus.CANCELED:
                msg = f"subscription {subscription_id} is already canceled"
                raise InvalidBillingOperationError(msg)

            status = SubscriptionStatus.ACTIVE if at_period_end else SubscriptionStatus.CANCELED
            updated = subscription.model_copy(update={"status": status})
            self._subscriptions[subscription_id] = updated
            log.info(
                "subscription_canceled",
                subscription_id=subscription_id,
                at_period_end=at_period_end,
            )
            return updated
