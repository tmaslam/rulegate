"""Resolves the facts a policy decision needs, before any rule runs.

Why this is not in ``policy/``
------------------------------
Fetching a charge is I/O. Rules are pure. Putting the fetch inside a rule would
make every rule async, make every rule test need a billing fixture, and let a
rule's verdict depend on when it happened to run.

So the split is: **this module gathers the facts, ``policy/`` judges them.** The
engine receives a fully-populated
:class:`~policy_guarded_ops_agent.policy.models.PolicyContext` and does no I/O
at all — which is exactly why ``policy/rules.py`` contains no ``await`` and its
tests need no database.

Missing entities are not errors here
------------------------------------
A charge that does not exist resolves to ``charge=None``, and
``EntityMustExistRule`` turns that into a named DENY. Raising here instead would
rob the customer of an explanation and the trail of a decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog

from policy_guarded_ops_agent.billing.api import NotFoundError
from policy_guarded_ops_agent.domain.actions import (
    CancelSubscriptionAction,
    ChangePlanAction,
    GetCustomerAction,
    GetSubscriptionAction,
    IssueRefundAction,
)
from policy_guarded_ops_agent.policy.models import PolicyContext

if TYPE_CHECKING:
    from datetime import datetime

    from policy_guarded_ops_agent.billing.api import MockBillingAPI
    from policy_guarded_ops_agent.domain.actions import ProposedAction
    from policy_guarded_ops_agent.domain.models import Charge, Customer, Subscription

__all__ = ["resolve_policy_context"]

log: Final = structlog.get_logger(__name__)


async def _try_get_charge(billing: MockBillingAPI, charge_id: str) -> Charge | None:
    try:
        return await billing.get_charge(charge_id)
    except NotFoundError:
        return None


async def _try_get_subscription(
    billing: MockBillingAPI, subscription_id: str
) -> Subscription | None:
    try:
        return await billing.get_subscription(subscription_id)
    except NotFoundError:
        return None


async def _try_get_customer(billing: MockBillingAPI, customer_id: str) -> Customer | None:
    try:
        return await billing.get_customer(customer_id)
    except NotFoundError:
        return None


async def resolve_policy_context(
    billing: MockBillingAPI,
    action: ProposedAction,
    *,
    now: datetime,
    customer_id: str | None = None,
) -> PolicyContext:
    """Fetch exactly the facts the rules need to judge ``action``.

    Fetches are scoped to the action type — a plan change does not load charges.
    Least privilege applied to *data*: a fact that was never loaded cannot be
    leaked into a prompt or accidentally reasoned over.

    Args:
        billing: The billing API to read from.
        action: The proposed action.
        now: The evaluation instant. Injected so the rules and the record agree.
        customer_id: Conversation's customer, loaded for context when known.

    Returns:
        A populated context. Unresolvable entities are ``None``, which the rules
        handle as a DENY rather than a crash.
    """
    charge: Charge | None = None
    subscription: Subscription | None = None
    customer: Customer | None = None

    match action:
        case IssueRefundAction():
            charge = await _try_get_charge(billing, action.charge_id)
            if charge is not None:
                subscription = (
                    await _try_get_subscription(billing, charge.subscription_id)
                    if charge.subscription_id is not None
                    else None
                )
        case ChangePlanAction() | CancelSubscriptionAction():
            subscription = await _try_get_subscription(billing, action.subscription_id)
        case GetSubscriptionAction():
            subscription = await _try_get_subscription(billing, action.subscription_id)
        case GetCustomerAction():
            customer = await _try_get_customer(billing, action.customer_id)
        case _:
            # reply / escalate need no facts: no rule reasons about them.
            pass

    if customer is None:
        # Prefer the entity's owner over the conversation's claimed customer:
        # the charge knows who it belongs to, the conversation only asserts it.
        resolved_customer_id = (
            charge.customer_id
            if charge is not None
            else subscription.customer_id
            if subscription is not None
            else customer_id
        )
        if resolved_customer_id is not None:
            customer = await _try_get_customer(billing, resolved_customer_id)

    return PolicyContext(now=now, customer=customer, subscription=subscription, charge=charge)
