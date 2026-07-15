# Security — policy-guarded-ops-agent

> Placeholders: `policy-guarded-ops-agent`, `tmaslam@gmail.com`, `https://github.com/tmaslam/rulegate`.
> **LLM01 and LLM06 below are filled in and apply as written.** The remaining
> entries are marked `TO BE COMPLETED PER PROJECT` — fill each one in with what
> *this* project actually does. An unedited stub is worse than an absent
> section: it claims a control that does not exist.

This is a **demonstration project**, not a production deployment, and not client
work. It is built to production patterns so the engineering is reviewable, but
it has not been through a professional penetration test or a third-party audit.
Nothing below should be read as a certification. Where a control is partial, it
says so.

Mapped to the [OWASP Top 10 for LLM Applications (2025)](https://owasp.org/www-project-top-10-for-large-language-model-applications/).

## Reporting a vulnerability

Email tmaslam@gmail.com with reproduction steps. Please do not open a public
issue for anything exploitable. This is a personal demo repo maintained on a
best-effort basis — expect a reply within a week, and no bounty.

---

## LLM01 — Prompt Injection

**Threat.** An attacker embeds instructions in content the model reads, causing
it to ignore its system prompt, exfiltrate context, or misuse a tool. Two forms
matter here:

- **Direct**: the end user types the injection.
- **Indirect**: the payload arrives inside a retrieved document, a web page, a
  file, or a tool result — content the *system* chose to read. This is the
  dangerous one, because the content is trusted by default and no human sees it
  before the model does.

**Position taken by this repo: prompt injection is not solved, and this repo does
not claim to solve it.** There is no known reliable defence. Pattern matching is
evaded by paraphrase, encoding, translation, or splitting a payload across turns.
Any project claiming a "prompt injection filter" that stops attacks is either
mistaken or selling something. The controls below **raise cost and improve
auditability**; they do not make the system safe against a motivated attacker.

### Controls implemented

| Control | Where | What it actually buys |
| --- | --- | --- |
| Trust boundary on untrusted content | `guardrails/base.py` → `InputContext.is_untrusted_source` | Retrieved/tool content is tagged at the boundary. Injection phrasings in it are **blocked**, not just logged — such content has no legitimate reason to issue instructions. |
| Heuristic pattern filter | `PromptInjectionHeuristicFilter` | Catches low-effort and accidental cases; produces an audit trail. Evadable. Deliberately does **not** block direct user input on a pattern hit (a user quoting an article about injection is not an attacker), so it trades away some coverage for a much lower false-refusal rate. |
| Structured output, not free text | `Gateway.acomplete_model` | The model returns JSON validated against a Pydantic schema. No regex scraping of model output, so injected prose cannot smuggle itself into a parsed field. |
| Deterministic code does the work | Architecture | The LLM *decides*; Python *acts*. An injected instruction can change a decision; it cannot invent an action that the code does not already implement. |
| Secret-leak backstop on output | `SecretLeakageFilter` | If injection persuades the model to echo a credential, the response is **blocked** (not redacted) so the incident surfaces instead of being served with a hole in it. |
| No secrets in the model's context | `.env` / `llm/gateway.py` | Keys are read from the environment at the transport layer and never placed in a prompt. There is nothing in context to exfiltrate. |

### Residual risk — accepted, not mitigated

- A novel phrasing bypasses the heuristic filter. **Assumed to happen.**
- An injection that only changes *content* (e.g. a wrong summary) is not caught
  by any filter here. Detection would need an eval, not a guardrail.
- No measured bypass rate is reported, because none has been measured. See
  COST.md/README.md conventions: **`not yet run`, not a guess.**

---

## LLM06 — Excessive Agency

**Threat.** The system can take actions disproportionate to what it needs — too
many tools, too much permission, or no human in the loop on something
irreversible. Injection (LLM01) is how an attacker gets in; excessive agency is
what determines the blast radius once they do. **This is the control that
actually matters**, because it holds even when LLM01 fails.

### Controls implemented

| Control | Where | Rationale |
| --- | --- | --- |
| Least tool privilege | Tool registry per project | The model is given the minimum tool set for the task. Every tool is an expansion of the blast radius. |
| Read/write split | Tool definitions | Read-only tools are unrestricted; state-changing tools are enumerated explicitly and kept few. |
| No raw code/SQL execution | Architecture | The model selects from **parameterised, allow-listed** operations. It never emits code or SQL that is executed. This is the single highest-leverage control here: it converts "arbitrary execution" into "choose from a fixed menu". |
| Schema-validated tool arguments | Pydantic v2 boundaries | Arguments are validated before any side effect. A malformed or coerced argument fails closed. |
| Scope enforcement | `AllowedTopicsFilter` | A system that answers anything can be talked into anything. Scope is enforced in code, not requested in the prompt. |
| Budget ceiling | `BudgetLedger` in `llm/gateway.py` | Caps spend per virtual key. Free tiers are $0, so this is a tripwire against an accidental swap to a metered provider — it turns a surprise bill into a refused request. |
| Rate limiting + circuit breaker | `TokenBucket`, `CircuitBreaker` | Bounds the damage rate of a runaway loop, and stops a dead provider from consuming the full retry schedule on every request. |
| No autonomous irreversible actions | Architecture | Nothing here deletes data, sends mail, moves money, or writes to a third party without an explicit human step. |
| Idempotency keys | `CompletionRequest.idempotency_key` | A retried request cannot double-execute. |

### Residual risk — accepted, not mitigated

- A tool that is *correctly* invoked with *attacker-chosen* arguments still runs.
  Schema validation constrains the shape, not the intent.
- Human-in-the-loop is architectural here (nothing irreversible exists to gate).
  A project that adds an irreversible action **must** add a confirmation step —
  update this section when that happens.

---

## LLM02 — Sensitive Information Disclosure

`TO BE COMPLETED PER PROJECT.`

Baseline that applies to every project in this portfolio:

- Secrets live in `.env` (gitignored); only `.env.example` is committed.
- `gitleaks` runs as a pre-commit hook, and `detect-private-key` alongside it.
- `SecretLeakageFilter` blocks credential-shaped strings in model output.
- `PIIRedactionFilter` redacts emails and Luhn-valid card numbers. It is **not**
  a compliance control: it misses names, addresses, and anything contextual.
- Trace bodies are off by default outside local (`LANGFUSE_CAPTURE_BODIES`).
- **Free-tier providers may train on your data.** Google AI Studio's free tier in
  particular may use prompts to improve products. Send nothing confidential.

Document per project: what data classes flow through, what is retained, where.

## LLM03 — Supply Chain

`TO BE COMPLETED PER PROJECT.`

Baseline: `uv.lock` is committed and CI installs with `--locked`; the Dockerfile
pins a uv minor and a Python patch; dependencies are constrained to a major.

Document per project: any model weights pulled, their provenance and licence.

## LLM04 — Data and Model Poisoning

`TO BE COMPLETED PER PROJECT.`

Baseline: the golden dataset is versioned in git **by content hash**, so a number
is always attributable to exact bytes, and a silent dataset edit cannot be passed
off as a score improvement — the CI gate refuses to compare across hashes.

Document per project: provenance of any ingested corpus.

## LLM05 — Improper Output Handling

`TO BE COMPLETED PER PROJECT.`

Baseline: outputs are schema-validated (`acomplete_model`), never `eval`'d, never
interpolated into SQL, never executed. Any project rendering model output in HTML
must state its escaping strategy here.

## LLM07 — System Prompt Leakage

`TO BE COMPLETED PER PROJECT.`

Baseline: assume the system prompt is public. It contains no secrets and no
access-control logic, so leaking it costs nothing. Authorisation is enforced in
code, never by asking the model to keep something quiet.

## LLM08 — Vector and Embedding Weaknesses

`TO BE COMPLETED PER PROJECT.` (Omit if the project has no retrieval.)

Baseline for retrieval projects: retrieved chunks are treated as **untrusted
input** and pass through the input guardrails with `is_untrusted_source=True`.
`GroundednessFilter` rejects citations to chunks that were never retrieved.

Document per project: tenant isolation in the vector store, if multi-tenant.

## LLM09 — Misinformation

`TO BE COMPLETED PER PROJECT.`

Baseline: abstention is a first-class outcome, not a failure —
`AbstentionFilter` turns a hedged answer into a structured, countable
abstention, and `GroundednessFilter` abstains rather than answer ungrounded.
Every quality claim in README.md carries a dataset, split, model+version, temp,
seed, scaffold and a 95% CI, or it reads `not yet run`.

## LLM10 — Unbounded Consumption

`TO BE COMPLETED PER PROJECT.`

Baseline: per-request timeout; `MaxLengthFilter` on input size; `TokenBucket`
per provider; `CircuitBreaker` per provider; `BudgetLedger` ceiling; bounded
retry schedule (1/2/4/8s, then fail over — never unbounded).

---

## Threat model boundaries

**In scope:** the application code in https://github.com/tmaslam/rulegate, its prompt/tool surface, its
handling of untrusted retrieved content, and its dependency manifest.

**Out of scope:** the security of the free-tier providers themselves (Groq,
Google AI Studio, Cerebras, OpenRouter), of Neon, of Langfuse Cloud, and of the
hosting platform. Their trust boundaries are theirs.

**Explicitly not claimed:** no penetration test, no third-party audit, no formal
verification, no compliance certification (SOC 2 / GDPR / HIPAA). This is a demo.
