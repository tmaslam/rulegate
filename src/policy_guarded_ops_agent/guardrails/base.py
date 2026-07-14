"""Per-request input/output guardrails with an explicit refusal/abstention path.

Guardrails vs. evals
--------------------
These are **not** evals. The distinction matters and is worth stating plainly:

* **Guardrails (this module)** run on *every single request*, in the request's
  critical path, and can change what the user gets. They must be fast, cheap and
  deterministic. Everything here is plain Python — no LLM call, so no latency
  tax, no per-request cost, and no dependency on a provider being up.
* **Evals (``evals/harness.py``)** run in batch, offline, against a versioned
  golden dataset. They measure quality. They never touch production traffic.

Conflating the two is a common and expensive mistake: an LLM-judge in the request
path doubles cost and latency and adds a second thing that can 429 on you.

What these filters honestly do
------------------------------
The input filters here are **heuristics, and heuristics are not a security
boundary**. A determined prompt injection will get past regex matching — see
SECURITY.md (LLM01). The real mitigation is architectural: least privilege on
tools, no secrets in context, human confirmation for irreversible actions, and
treating all retrieved/tool content as untrusted. These filters raise the cost of
casual attacks and produce a clean audit trail. They do not make the system safe,
and this repo does not claim they do.

Copy to ``src/<package>/guardrails/base.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AbstentionFilter",
    "AllowedTopicsFilter",
    "GroundednessFilter",
    "GuardrailDecision",
    "GuardrailPipeline",
    "InputContext",
    "InputFilter",
    "MaxLengthFilter",
    "OutputContext",
    "OutputFilter",
    "PIIRedactionFilter",
    "PromptInjectionHeuristicFilter",
    "Refusal",
    "RefusalCode",
    "SecretLeakageFilter",
    "Verdict",
    "default_input_filters",
    "default_output_filters",
]

log: Final = structlog.get_logger(__name__)

# Luhn / payment-card constants.
_LUHN_DIGIT_WRAP: Final = 9
_CARD_MIN_DIGITS: Final = 13
_CARD_MAX_DIGITS: Final = 19


class Verdict(StrEnum):
    """What a filter decided."""

    #: Content is fine as-is.
    ALLOW = "allow"
    #: Content was modified (e.g. PII redacted) and may proceed.
    REDACT = "redact"
    #: Content must not proceed. Short-circuits the pipeline.
    BLOCK = "block"
    #: The system declines to answer, without asserting wrongdoing. Used when the
    #: model cannot answer *groundedly* — an honest "I don't know" beats a
    #: confident fabrication.
    ABSTAIN = "abstain"


class RefusalCode(StrEnum):
    """Machine-readable refusal reason. Stable — dashboards and tests key off it."""

    INPUT_TOO_LONG = "input_too_long"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"
    OFF_TOPIC = "off_topic"
    # S105 is suppressed below: this is a refusal *reason code*, not a credential.
    SECRET_LEAK_PREVENTED = "secret_leak_prevented"  # noqa: S105
    UNGROUNDED = "ungrounded"
    LOW_CONFIDENCE = "low_confidence"
    POLICY = "policy"


class Refusal(BaseModel):
    """A structured refusal or abstention.

    ``user_message`` is safe to display. ``reason`` is for logs and must not be
    shown verbatim — telling an attacker exactly which pattern tripped is free
    reconnaissance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: RefusalCode
    user_message: str
    reason: str
    filter_name: str
    #: True for abstention (cannot answer well) vs. refusal (will not answer).
    is_abstention: bool = False


class FilterResult(BaseModel):
    """One filter's decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    filter_name: str
    #: Rewritten content when verdict is REDACT; otherwise None.
    content: str | None = None
    refusal: Refusal | None = None
    #: Diagnostics for tracing. Never shown to the user.
    details: Mapping[str, str] = Field(default_factory=dict)

    @classmethod
    def allow(cls, filter_name: str) -> FilterResult:
        """Pass-through result."""
        return cls(verdict=Verdict.ALLOW, filter_name=filter_name)


class InputContext(BaseModel):
    """What an input filter sees."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    #: True when this text came from a document, tool result or web page rather
    #: than the end user. Untrusted content deserves stricter treatment — this is
    #: the single most useful signal for LLM01.
    is_untrusted_source: bool = False
    metadata: Mapping[str, str] = Field(default_factory=dict)


class OutputContext(BaseModel):
    """What an output filter sees."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    #: The prompt that produced this output, for groundedness checks.
    prompt: str = ""
    #: Ids of chunks supplied to the model, for citation validation.
    retrieved_ids: tuple[str, ...] = ()
    metadata: Mapping[str, str] = Field(default_factory=dict)


@runtime_checkable
class InputFilter(Protocol):
    """A filter applied to inbound content on every request."""

    @property
    def name(self) -> str:
        """Stable identifier, used in logs and refusals."""
        ...

    def check(self, ctx: InputContext) -> FilterResult:
        """Evaluate ``ctx``. Must be pure, fast and free of I/O."""
        ...


@runtime_checkable
class OutputFilter(Protocol):
    """A filter applied to model output on every request."""

    @property
    def name(self) -> str:
        """Stable identifier, used in logs and refusals."""
        ...

    def check(self, ctx: OutputContext) -> FilterResult:
        """Evaluate ``ctx``. Must be pure, fast and free of I/O."""
        ...


class GuardrailDecision(BaseModel):
    """Outcome of running a pipeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    #: Final content after any redactions. None when blocked.
    content: str | None
    refusal: Refusal | None = None
    #: Every filter that did something, in order. The audit trail.
    applied: tuple[str, ...] = ()

    @property
    def is_abstention(self) -> bool:
        """True when the pipeline abstained rather than refused."""
        return self.refusal is not None and self.refusal.is_abstention


# ---------------------------------------------------------------------------
# Input filters
# ---------------------------------------------------------------------------


class MaxLengthFilter:
    """Reject oversized input.

    First line of defence against cost blowups and context-stuffing. Cheap, exact,
    and unlike most guardrails it is genuinely a hard boundary rather than a
    heuristic.
    """

    def __init__(self, *, max_chars: int = 32_000) -> None:
        self._max_chars = max_chars

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "max_length"

    def check(self, ctx: InputContext) -> FilterResult:
        """Block input exceeding the character ceiling."""
        if len(ctx.text) <= self._max_chars:
            return FilterResult.allow(self.name)
        return FilterResult(
            verdict=Verdict.BLOCK,
            filter_name=self.name,
            refusal=Refusal(
                code=RefusalCode.INPUT_TOO_LONG,
                user_message=(
                    f"That input is too long. Please keep it under {self._max_chars:,} characters."
                ),
                reason=f"input was {len(ctx.text)} chars, limit {self._max_chars}",
                filter_name=self.name,
            ),
            details={"length": str(len(ctx.text))},
        )


class PromptInjectionHeuristicFilter:
    """Flag common prompt-injection phrasings. **A heuristic, not a boundary.**

    Read this before relying on it: pattern matching cannot stop prompt injection.
    Attacks trivially evade it via paraphrase, encoding, translation, or splitting
    the payload across turns. Published bypass rates against regex filters are
    high, and this repo reports no number here because none has been measured.

    What it is actually worth:

    * It catches low-effort and accidental cases, which are most of them.
    * It creates an audit trail of attempts.
    * On ``is_untrusted_source=True`` content it escalates to BLOCK, which *is*
      meaningful — injection payloads arriving inside a retrieved document have
      no legitimate reason to say "ignore previous instructions".

    The load-bearing mitigations are in SECURITY.md under LLM01/LLM06: least
    privilege on tools, no secrets in context, and confirmation gates on
    irreversible actions.
    """

    #: Deliberately conservative — a false positive on user input is a refused
    #: legitimate request, which is its own kind of failure.
    DEFAULT_PATTERNS: Final[tuple[str, ...]] = (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+instructions?",
        r"disregard\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?)",
        r"forget\s+(?:everything|all)\s+(?:you|above|before)",
        r"you\s+are\s+now\s+(?:a|an|in)\s+\w+\s+mode",
        r"(?:reveal|print|show|repeat|output)\s+(?:your|the)\s+(?:system\s+prompt|instructions|initial\s+prompt)",
        r"\bDAN\s+mode\b",
        r"developer\s+mode\s+enabled",
        r"<\s*/?\s*(?:system|assistant)\s*>",  # fake role delimiters
        r"\[\s*(?:system|INST)\s*\]",
    )

    def __init__(self, *, patterns: Sequence[str] | None = None) -> None:
        source = patterns if patterns is not None else self.DEFAULT_PATTERNS
        self._patterns = tuple(re.compile(p, re.IGNORECASE) for p in source)

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "prompt_injection_heuristic"

    def check(self, ctx: InputContext) -> FilterResult:
        """Flag suspicious phrasings; block them outright in untrusted content."""
        hits = [p.pattern for p in self._patterns if p.search(ctx.text)]
        if not hits:
            return FilterResult.allow(self.name)

        log.warning(
            "prompt_injection_suspected",
            untrusted=ctx.is_untrusted_source,
            hit_count=len(hits),
        )
        if ctx.is_untrusted_source:
            # Retrieved/tool content has no business issuing instructions.
            return FilterResult(
                verdict=Verdict.BLOCK,
                filter_name=self.name,
                refusal=Refusal(
                    code=RefusalCode.PROMPT_INJECTION_SUSPECTED,
                    user_message=("I couldn't process that source safely, so I've skipped it."),
                    reason=f"injection pattern in untrusted content: {hits[0]}",
                    filter_name=self.name,
                ),
                details={"hits": str(len(hits))},
            )
        # Direct user input: flag for tracing but allow. A user quoting an article
        # about prompt injection is not an attacker, and blocking them is a worse
        # error than logging them.
        return FilterResult(
            verdict=Verdict.ALLOW,
            filter_name=self.name,
            details={"suspected": "true", "hits": str(len(hits))},
        )


class AllowedTopicsFilter:
    """Keep the system inside its declared scope.

    Scope control is a real LLM06 (excessive agency) mitigation: a support bot
    that will answer anything is a support bot that will be talked into anything.

    Keyword-based and therefore imprecise. ``require_match=False`` (the default)
    only blocks explicit deny-terms; set it True for allowlist behaviour, which is
    stricter and will refuse some legitimate phrasings.
    """

    def __init__(
        self,
        *,
        allowed_keywords: Sequence[str] = (),
        denied_keywords: Sequence[str] = (),
        require_match: bool = False,
    ) -> None:
        self._allowed = tuple(k.lower() for k in allowed_keywords)
        self._denied = tuple(k.lower() for k in denied_keywords)
        self._require_match = require_match

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "allowed_topics"

    def check(self, ctx: InputContext) -> FilterResult:
        """Block denied topics, and off-scope input when an allowlist is enforced."""
        lowered = ctx.text.lower()
        denied_hit = next((k for k in self._denied if k in lowered), None)
        if denied_hit is not None:
            return self._refuse(f"denied keyword: {denied_hit}")
        if self._require_match and self._allowed and not any(k in lowered for k in self._allowed):
            return self._refuse("no allowed keyword matched")
        return FilterResult.allow(self.name)

    def _refuse(self, reason: str) -> FilterResult:
        return FilterResult(
            verdict=Verdict.BLOCK,
            filter_name=self.name,
            refusal=Refusal(
                code=RefusalCode.OFF_TOPIC,
                user_message="That's outside what I can help with here.",
                reason=reason,
                filter_name=self.name,
            ),
        )


class PIIRedactionFilter:
    """Redact obvious PII in place. Works as an input or output filter.

    Catches emails, phone numbers and card-shaped digit runs. Card matches are
    Luhn-validated to cut false positives on order numbers.

    **Not a compliance control.** It will miss names, addresses, and anything
    context-dependent. It reduces incidental leakage into logs and third-party
    providers; it does not make the system GDPR- or HIPAA-compliant, and this repo
    makes no such claim.
    """

    _EMAIL: Final = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _PHONE: Final = re.compile(
        r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}(?!\d)"
    )
    _CARD: Final = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

    def __init__(
        self,
        *,
        redact_emails: bool = True,
        redact_phones: bool = False,
        redact_cards: bool = True,
    ) -> None:
        # Phone redaction defaults off: the pattern is broad enough to eat
        # order numbers, dates and version strings. Enable it deliberately.
        self._redact_emails = redact_emails
        self._redact_phones = redact_phones
        self._redact_cards = redact_cards

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "pii_redaction"

    @staticmethod
    def _luhn_ok(digits: str) -> bool:
        """Luhn checksum — separates real card numbers from arbitrary digit runs."""
        total = 0
        parity = len(digits) % 2
        for index, char in enumerate(digits):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > _LUHN_DIGIT_WRAP:
                    digit -= _LUHN_DIGIT_WRAP
            total += digit
        return total % 10 == 0

    def _redact_card_match(self, match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        if _CARD_MIN_DIGITS <= len(digits) <= _CARD_MAX_DIGITS and self._luhn_ok(digits):
            return "[REDACTED_CARD]"
        return match.group()

    def _apply(self, text: str) -> tuple[str, int]:
        count = 0
        if self._redact_emails:
            text, hits = self._EMAIL.subn("[REDACTED_EMAIL]", text)
            count += hits
        if self._redact_cards:
            # Count by delta rather than subn(): the replacement is conditional
            # on the Luhn check, so subn() would over-count non-card digit runs
            # that were matched but deliberately left untouched.
            before = text.count("[REDACTED_CARD]")
            text = self._CARD.sub(self._redact_card_match, text)
            count += text.count("[REDACTED_CARD]") - before
        if self._redact_phones:
            text, hits = self._PHONE.subn("[REDACTED_PHONE]", text)
            count += hits
        return text, count

    def check(self, ctx: InputContext | OutputContext) -> FilterResult:
        """Redact PII, returning REDACT when anything changed."""
        redacted, count = self._apply(ctx.text)
        if count == 0:
            return FilterResult.allow(self.name)
        return FilterResult(
            verdict=Verdict.REDACT,
            filter_name=self.name,
            content=redacted,
            details={"redactions": str(count)},
        )


# ---------------------------------------------------------------------------
# Output filters
# ---------------------------------------------------------------------------


class SecretLeakageFilter:
    """Block output containing credential-shaped strings.

    Defence against an injected prompt persuading the model to echo an env var or
    key that reached its context. The real fix is not putting secrets in context
    at all (SECURITY.md, LLM02/LLM06); this is the backstop for when that fails.

    Blocks rather than redacts: if a key is in the output, something upstream is
    already wrong and quietly serving the rest of the message hides the incident.
    """

    _PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
        ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
        ("groq", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
        ("google", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
        ("openrouter", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b")),
        ("langfuse", re.compile(r"\b(?:pk|sk)-lf-[A-Za-z0-9-]{20,}\b")),
        ("aws", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
        ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
        ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("pg_url", re.compile(r"postgres(?:ql)?://[^\s:]+:[^\s@]+@")),
    )

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "secret_leakage"

    def check(self, ctx: OutputContext) -> FilterResult:
        """Block any output carrying a credential-shaped string."""
        for label, pattern in self._PATTERNS:
            if pattern.search(ctx.text):
                log.error("secret_leak_blocked", kind=label)
                return FilterResult(
                    verdict=Verdict.BLOCK,
                    filter_name=self.name,
                    refusal=Refusal(
                        code=RefusalCode.SECRET_LEAK_PREVENTED,
                        user_message=(
                            "I can't share that response. Please contact support "
                            "if you expected an answer here."
                        ),
                        reason=f"{label} credential pattern in model output",
                        filter_name=self.name,
                    ),
                    details={"kind": label},
                )
        return FilterResult.allow(self.name)


class GroundednessFilter:
    """Require every citation to reference a chunk actually retrieved.

    Deterministic and exact: it does not judge whether the *claim* is supported —
    that needs an eval — but it does catch the model inventing a source id, which
    is a real and common failure. Cheap, and no LLM call.

    Citations are expected as ``[id]``. Override ``citation_pattern`` to match
    your format.
    """

    def __init__(
        self,
        *,
        citation_pattern: str = r"\[([A-Za-z0-9_\-.:]+)\]",
        require_citation: bool = False,
    ) -> None:
        self._pattern = re.compile(citation_pattern)
        #: When True, an answer with no citation at all abstains. Only enable for
        #: strictly grounded surfaces — it will abstain on "hello".
        self._require_citation = require_citation

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "groundedness"

    def check(self, ctx: OutputContext) -> FilterResult:
        """Abstain when a citation names a chunk that was never retrieved."""
        cited = set(self._pattern.findall(ctx.text))
        allowed = set(ctx.retrieved_ids)

        if not cited:
            if self._require_citation and allowed:
                return self._abstain("answer contained no citation")
            return FilterResult.allow(self.name)

        invented = cited - allowed
        if invented:
            return self._abstain(f"cited non-retrieved ids: {sorted(invented)}")
        return FilterResult(
            verdict=Verdict.ALLOW,
            filter_name=self.name,
            details={"citations": str(len(cited))},
        )

    def _abstain(self, reason: str) -> FilterResult:
        log.warning("groundedness_abstain", reason=reason)
        return FilterResult(
            verdict=Verdict.ABSTAIN,
            filter_name=self.name,
            refusal=Refusal(
                code=RefusalCode.UNGROUNDED,
                user_message=(
                    "I don't have enough grounded information to answer that accurately."
                ),
                reason=reason,
                filter_name=self.name,
                is_abstention=True,
            ),
        )


class AbstentionFilter:
    """Turn hedged non-answers into an explicit, honest abstention.

    When a model says "I don't know", that is a *good* outcome that should be
    surfaced as a first-class abstention — countable, traceable, and never dressed
    up as a confident answer. Normalising it here means abstention rate becomes a
    metric instead of noise buried in free text.
    """

    DEFAULT_MARKERS: Final[tuple[str, ...]] = (
        "i don't know",
        "i do not know",
        "i'm not sure",
        "i am not sure",
        "cannot determine",
        "no information available",
        "unable to answer",
        "not enough context",
    )

    def __init__(
        self,
        *,
        markers: Sequence[str] = DEFAULT_MARKERS,
        user_message: str = "I don't have enough information to answer that reliably.",
    ) -> None:
        self._markers = tuple(m.lower() for m in markers)
        self._user_message = user_message

    @property
    def name(self) -> str:
        """Filter identifier."""
        return "abstention"

    def check(self, ctx: OutputContext) -> FilterResult:
        """Convert a hedged answer into a structured abstention."""
        lowered = ctx.text.lower()
        hit = next((m for m in self._markers if m in lowered), None)
        if hit is None:
            return FilterResult.allow(self.name)
        return FilterResult(
            verdict=Verdict.ABSTAIN,
            filter_name=self.name,
            refusal=Refusal(
                code=RefusalCode.LOW_CONFIDENCE,
                user_message=self._user_message,
                reason=f"model signalled uncertainty: {hit!r}",
                filter_name=self.name,
                is_abstention=True,
            ),
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class GuardrailPipeline:
    """Compose filters and run them on every request.

    Filters run in order. REDACT results feed the rewritten content to the next
    filter, so redactions compose. BLOCK and ABSTAIN short-circuit immediately —
    later filters never see content already rejected.

    Example::

        pipeline = GuardrailPipeline(
            input_filters=default_input_filters(),
            output_filters=default_output_filters(),
        )
        decision = pipeline.check_input(InputContext(text=user_text))
        if not decision.allowed:
            return decision.refusal.user_message
        response = await gateway.acomplete(user_request(decision.content))
        out = pipeline.check_output(OutputContext(text=response.text))
    """

    def __init__(
        self,
        *,
        input_filters: Sequence[InputFilter] = (),
        output_filters: Sequence[OutputFilter] = (),
    ) -> None:
        self._input_filters = tuple(input_filters)
        self._output_filters = tuple(output_filters)

    @property
    def input_filter_names(self) -> tuple[str, ...]:
        """Names of configured input filters, in run order."""
        return tuple(f.name for f in self._input_filters)

    @property
    def output_filter_names(self) -> tuple[str, ...]:
        """Names of configured output filters, in run order."""
        return tuple(f.name for f in self._output_filters)

    def check_input(self, ctx: InputContext) -> GuardrailDecision:
        """Run every input filter against ``ctx``."""
        text = ctx.text
        applied: list[str] = []
        for filt in self._input_filters:
            result = filt.check(ctx.model_copy(update={"text": text}))
            decision = self._fold(result, applied)
            if decision is not None:
                return decision
            if result.verdict is Verdict.REDACT and result.content is not None:
                text = result.content
        return GuardrailDecision(allowed=True, content=text, applied=tuple(applied))

    def check_output(self, ctx: OutputContext) -> GuardrailDecision:
        """Run every output filter against ``ctx``."""
        text = ctx.text
        applied: list[str] = []
        for filt in self._output_filters:
            result = filt.check(ctx.model_copy(update={"text": text}))
            decision = self._fold(result, applied)
            if decision is not None:
                return decision
            if result.verdict is Verdict.REDACT and result.content is not None:
                text = result.content
        return GuardrailDecision(allowed=True, content=text, applied=tuple(applied))

    @staticmethod
    def _fold(result: FilterResult, applied: list[str]) -> GuardrailDecision | None:
        """Return a terminal decision for BLOCK/ABSTAIN, else None to continue."""
        if result.verdict is not Verdict.ALLOW:
            applied.append(result.filter_name)
        if result.verdict in {Verdict.BLOCK, Verdict.ABSTAIN}:
            if result.refusal is None:
                # A terminal verdict with no refusal would leave the caller with
                # nothing to say to the user. Fail loudly at wiring time.
                msg = (
                    f"filter {result.filter_name!r} returned {result.verdict} "
                    f"without a Refusal; every terminal verdict must carry one"
                )
                raise ValueError(msg)
            log.info(
                "guardrail_terminal",
                filter=result.filter_name,
                verdict=str(result.verdict),
                code=str(result.refusal.code),
            )
            return GuardrailDecision(
                allowed=False,
                content=None,
                refusal=result.refusal,
                applied=tuple(applied),
            )
        return None


def default_input_filters(*, max_chars: int = 32_000) -> tuple[InputFilter, ...]:
    """A sane starting set. Order matters: cheapest and most decisive first."""
    return (
        MaxLengthFilter(max_chars=max_chars),
        PromptInjectionHeuristicFilter(),
        PIIRedactionFilter(),
    )


def default_output_filters() -> tuple[OutputFilter, ...]:
    """A sane starting set for outbound content."""
    return (
        SecretLeakageFilter(),
        AbstentionFilter(),
    )
