"""Domain guardrails for customer-ops traffic.

These run on **every** request, before any model call, and they are cheap
deterministic Python — no LLM, so no latency tax and no second thing that can
429 on you.

Scope, honestly stated
----------------------
These are **not** the policy engine, and they are not a security boundary. They
are a cheap first filter that keeps obvious junk out of the model's context and
leaves an audit trail of attempts. A determined prompt injection walks past
keyword matching; see SECURITY.md (LLM01).

The load-bearing control in this system is architectural, not textual: the model
can only emit a
:data:`~policy_guarded_ops_agent.domain.actions.ProposedAction`, and every
effectful one of those is gated by ``policy/``. An injection that successfully
talks the model into proposing a 60-day refund still gets refused by
``refund-window-30d``, because the rule never reads the prompt.
"""

from __future__ import annotations

from typing import Final

from policy_guarded_ops_agent.guardrails.base import (
    AbstentionFilter,
    AllowedTopicsFilter,
    InputFilter,
    MaxLengthFilter,
    OutputFilter,
    PIIRedactionFilter,
    PromptInjectionHeuristicFilter,
    SecretLeakageFilter,
)

__all__ = ["DENIED_TOPICS", "ops_input_filters", "ops_output_filters"]

#: Requests this agent must not engage with at all. Narrow on purpose: a
#: false positive here is a refused legitimate customer, which is its own kind of
#: failure. These are things a *billing* bot has no business attempting, where
#: the right answer is always a human.
DENIED_TOPICS: Final[tuple[str, ...]] = (
    "chargeback",  # a card-network dispute; touching it can forfeit the case
    "legal action",
    "lawsuit",
    "subpoena",
    "gdpr erasure",
    "right to be forgotten",
)


def ops_input_filters(*, max_chars: int = 8_000) -> tuple[InputFilter, ...]:
    """Input filters for customer-ops traffic, cheapest and most decisive first.

    ``require_match=False``: only the deny-list blocks. An allowlist would refuse
    perfectly ordinary phrasings ("this is wrong, fix it") that never mention a
    billing keyword, and a support bot that refuses confused customers is
    useless — confused customers are the entire job.
    """
    return (
        MaxLengthFilter(max_chars=max_chars),
        AllowedTopicsFilter(denied_keywords=DENIED_TOPICS, require_match=False),
        PromptInjectionHeuristicFilter(),
        # Customers paste card numbers into support chats constantly. Redact
        # before the text reaches a third-party provider or a trace exporter.
        # Phones stay off: the pattern eats invoice and order numbers.
        PIIRedactionFilter(redact_emails=True, redact_cards=True, redact_phones=False),
    )


def ops_output_filters() -> tuple[OutputFilter, ...]:
    """Output filters for anything the agent says to a customer."""
    return (
        SecretLeakageFilter(),
        AbstentionFilter(),
    )
