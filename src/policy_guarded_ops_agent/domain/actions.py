"""The action vocabulary — the *only* thing the LLM is allowed to emit.

Why a closed union
------------------
The model does not write SQL, call a tool by name, or return prose that we then
pattern-match. It fills in exactly one member of :data:`ProposedAction`, a
discriminated union validated by Pydantic. Anything else is a hard
:class:`~pydantic.ValidationError` at the boundary, not a surprise three layers
down.

This is what makes the policy engine possible. A rule can only reason about an
action if the action has a *shape*; "the model said something about a refund" is
not a shape. Every field a rule needs — amount, target plan, proration flag — is
a typed field here, so ``policy/rules.py`` is plain arithmetic over data.

Read-only vs. effectful
-----------------------
:attr:`ActionType.is_effectful` splits the union in two. Reads (`get_customer`,
`get_subscription`) and `reply`/`escalate` change no customer state and are not
policy-gated. The three effectful actions — refund, plan change, cancel — are
the entire blast radius of this agent, and every one of them goes through
``policy/``. Keeping that list short and explicit *is* the LLM06
(excessive-agency) mitigation; see SECURITY.md.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from policy_guarded_ops_agent.domain.models import USD, PlanTier

__all__ = [
    "ActionType",
    "AgentProposal",
    "CancelSubscriptionAction",
    "ChangePlanAction",
    "EscalateAction",
    "GetCustomerAction",
    "GetSubscriptionAction",
    "IssueRefundAction",
    "ProposedAction",
    "ReplyAction",
]


class ActionType(StrEnum):
    """Every action the agent can propose."""

    GET_CUSTOMER = "get_customer"
    GET_SUBSCRIPTION = "get_subscription"
    ISSUE_REFUND = "issue_refund"
    CHANGE_PLAN = "change_plan"
    CANCEL_SUBSCRIPTION = "cancel"
    ESCALATE = "escalate"
    REPLY = "reply"

    @property
    def is_effectful(self) -> bool:
        """Whether this action mutates customer-visible billing state.

        Only these are policy-gated. This is the agent's entire blast radius,
        and it is deliberately three items long.
        """
        return self in {
            ActionType.ISSUE_REFUND,
            ActionType.CHANGE_PLAN,
            ActionType.CANCEL_SUBSCRIPTION,
        }


class _Action(BaseModel):
    """Shared config for every action: frozen, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GetCustomerAction(_Action):
    """Look up a customer. Read-only."""

    action: Literal[ActionType.GET_CUSTOMER] = ActionType.GET_CUSTOMER
    customer_id: str = Field(description="Id of the customer to fetch.")


class GetSubscriptionAction(_Action):
    """Look up a subscription. Read-only."""

    action: Literal[ActionType.GET_SUBSCRIPTION] = ActionType.GET_SUBSCRIPTION
    subscription_id: str = Field(description="Id of the subscription to fetch.")


class IssueRefundAction(_Action):
    """Refund part or all of a settled charge. Effectful and irreversible."""

    action: Literal[ActionType.ISSUE_REFUND] = ActionType.ISSUE_REFUND
    charge_id: str = Field(description="Id of the charge to refund.")
    amount_usd: USD = Field(gt=Decimal(0), description="Refund amount in USD. Must be positive.")
    reason: str = Field(min_length=1, description="Why the refund is being issued.")


class ChangePlanAction(_Action):
    """Move a subscription to a different plan. Effectful."""

    action: Literal[ActionType.CHANGE_PLAN] = ActionType.CHANGE_PLAN
    subscription_id: str = Field(description="Id of the subscription to change.")
    target_plan: PlanTier = Field(description="The plan to move to.")
    prorate: bool = Field(
        default=False,
        description=(
            "Whether to prorate the change. A mid-cycle DOWNGRADE without "
            "proration is refused by policy: the customer has already paid for "
            "the richer plan through the end of the period, and silently "
            "dropping them to a cheaper one without crediting the difference "
            "takes money they are owed."
        ),
    )


class CancelSubscriptionAction(_Action):
    """Cancel a subscription. Effectful."""

    action: Literal[ActionType.CANCEL_SUBSCRIPTION] = ActionType.CANCEL_SUBSCRIPTION
    subscription_id: str = Field(description="Id of the subscription to cancel.")
    at_period_end: bool = Field(
        default=True,
        description=(
            "Cancel at the end of the paid period (default) rather than "
            "immediately. Immediate cancellation forfeits paid-for time."
        ),
    )


class EscalateAction(_Action):
    """Hand off to a human. Not policy-gated — escalating is always allowed."""

    action: Literal[ActionType.ESCALATE] = ActionType.ESCALATE
    reason: str = Field(min_length=1, description="Why this needs a human.")
    summary: str = Field(default="", description="Context for the human picking this up.")


class ReplyAction(_Action):
    """Answer the customer without touching billing state."""

    action: Literal[ActionType.REPLY] = ActionType.REPLY
    message: str = Field(min_length=1, description="The reply to send to the customer.")


#: Discriminated union of every proposable action, keyed on `action`. Pydantic
#: resolves the member from the tag, so an unknown/misspelled action is a
#: ValidationError at the boundary rather than a silent fall-through.
type ProposedAction = Annotated[
    GetCustomerAction
    | GetSubscriptionAction
    | IssueRefundAction
    | ChangePlanAction
    | CancelSubscriptionAction
    | EscalateAction
    | ReplyAction,
    Field(discriminator="action"),
]


class AgentProposal(BaseModel):
    """What the model returns: one action, plus its stated reasoning.

    ``reasoning`` is recorded in the audit trail and shown to a human reviewer.
    It is **explanation, never authority** — no rule reads it, and no decision
    depends on it. A model that argues eloquently for an illegal refund still
    gets the same deterministic refusal, which is the entire point.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning: str = Field(
        default="",
        description="Short justification for the chosen action. Never affects the outcome.",
    )
    action: ProposedAction = Field(description="The single action to take.")
