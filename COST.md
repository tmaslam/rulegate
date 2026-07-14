# Cost — {{PROJECT_NAME}}

> Placeholders: `{{PROJECT_NAME}}`.
>
> **Every value cell in this document reads `not yet run`.** That is not an
> oversight and it is not a placeholder to be filled with something plausible.
> A cell gets a number only when a measured run produced it, with the full
> provenance block attached. If you are tempted to write an estimate here,
> write `not yet run` instead and go run it.

## Summary

This project is built to run at **$0.00**. Every provider is a free tier that
needs no credit card. The interesting engineering question is therefore not
"what does it cost" — it is **"what would it cost if you moved it to a metered
provider, and how do you know?"** This document defines how that is measured, so
the number is reproducible instead of asserted.

## Methodology

### What "cost per request" means here

```
cost_per_request = (billable_input_tokens  × price_per_1M_input  / 1e6)
                 + (output_tokens          × price_per_1M_output / 1e6)

billable_input_tokens = prompt_tokens − cached_prompt_tokens
```

Rules the code actually enforces (`llm/gateway.py`, `evals/harness.py`):

1. **Token counts come from the provider's `usage` block**, never from a local
   estimate. A local tokenizer disagrees with the provider's billing tokenizer,
   and the provider's is the one that bills you.
2. **Cached prompt tokens are subtracted from billable input.** Where a provider
   supports prompt caching, cache hits are reported and excluded.
3. **Unknown price ⇒ `cost_usd = None`, never `0`.** `PriceSpec` returns `None`
   when the price list has no entry. A zero would silently under-report.
4. **A partially-priced run reports no total at all.** `EvalReport.total_cost_usd`
   returns `None` if *any* case had unknown pricing. Summing only the priced
   cases would produce an undercount presented as a total — a fabrication.
5. **$0.00 on a free tier is a fact about the price list, not a benchmark.**
   Free tiers are rate-limited rather than metered. `is_free_tier=True` means
   "this provider bills nothing", which is true and verifiable. It is not a
   measurement of efficiency and must never be presented as one.
6. **A fake-provider run costs $0 and proves nothing about cost.** Runs against
   `fakes/fake_llm.py` are stamped `scaffold_only=true` and the report says so.

### Provenance required for any number in this file

A cost figure is meaningless without the conditions that produced it. Every
populated row must carry, at minimum:

- dataset name + **content SHA-256** + split
- model **and version**, resolved (not the alias requested)
- temperature, seed, and the scaffold under test
- number of requests in the run, and the date
- p50 / p95 latency measured in the same run
- for any rate/score: a 95% confidence interval

`EvalReport.render_markdown()` emits exactly this block. Copy from it; do not
retype numbers by hand.

### How to actually measure it

```bash
# 1. Configure at least one real provider key in .env (free tiers, no card).
# 2. Run the eval against the live path rather than the fake.
#    (Each project wires its own scaffold; see SPINE.md step 6.)
uv run python -m evals.harness run --dataset evals/datasets/golden.v1.jsonl \
    --out evals/runs/live.json

# 3. Read the numbers out of the report. Do not retype them.
```

Because the reference free tiers price at $0, a *live* run against them yields a
true cost of $0.00 and a **real** latency distribution. To answer the "what if it
were metered" question, populate `PriceSpec` with a metered provider's published
prices and re-run — the token counts are real, so the projected cost is arithmetic
on measured data rather than a guess. **Label such a row `projected`, not
`measured`.**

---

## Measured cost per request

| Scenario | Provider | Model + version | Dataset (sha) / split | Requests | Temp / Seed | Scaffold | Tokens in / out | Cache hit rate | Cost / req | p50 / p95 latency | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Primary path | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run |
| Fallback path | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run |
| Cached prompt | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run |

## Projected cost on a metered provider

> Arithmetic on **measured token counts** using a published price list. Requires
> a completed live run above — without real token counts there is nothing to
> project from, and a projection built on guessed tokens is a guess.

| Provider | Model + version | Price in / out (per 1M) | Measured tokens in / out | Projected cost / req | Projected cost / 1k req | Price list dated |
| --- | --- | --- | --- | --- | --- | --- |
| not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run |

## Monthly run cost at the free tier

| Component | Provider | Free tier limit | Measured usage | Cost |
| --- | --- | --- | --- | --- |
| LLM inference | Groq / Gemini / Cerebras / OpenRouter `:free` | Rate-limited, not metered; no card | not yet run | $0.00 |
| Postgres + pgvector | Neon free tier | Per Neon's current free plan | not yet run | $0.00 |
| Tracing | Langfuse Cloud free tier | 50k observations / month | not yet run | $0.00 |
| CI | GitHub Actions | Free on public repos | not yet run | $0.00 |
| API hosting | Hugging Face Spaces / Render free | Per platform's current free plan | not yet run | $0.00 |
| UI hosting | Vercel | Per Vercel's current Hobby plan | not yet run | $0.00 |
| **Total** | | | | **$0.00** |

The `$0.00` column is a property of the price lists — each of these tiers bills
nothing. The **Measured usage** column is `not yet run` because usage has not
been measured, and quoting headroom against a quota without measuring it would
be a guess. Free-tier limits change without notice; verify against each
provider's current documentation before relying on this table.

## Cost controls in the code

| Control | Where | Effect |
| --- | --- | --- |
| Budget ceiling per virtual key | `BudgetLedger` | Refuses requests past `LLM_BUDGET_USD`. Defaults to `0.00`: a tripwire against an accidental swap to a metered provider. |
| Unpriced-call counter | `BudgetLedger.unpriced_calls` | Non-zero means the ledger is blind and spend is under-counted. Surfaced rather than hidden behind a zero. |
| Per-provider rate limiting | `TokenBucket` | Shapes traffic under published free-tier RPM. Cheaper than discovering the limit through 429s and backoff. |
| Circuit breaker | `CircuitBreaker` | A dead provider costs one probe per recovery window, not a full 1+2+4+8s retry schedule per request. |
| Prompt caching | `ProviderSpec.supports_prompt_cache` | Cached input tokens are excluded from billable input where the provider reports them. |
| Idempotency keys | `Gateway` dedupe window | A client retry cannot double-bill. |
| Bounded retries | `BACKOFF_SCHEDULE_S` | 4 retries max, then fail over. Never unbounded. |
| Fake provider in CI | `fakes/fake_llm.py` | The full test + eval suite runs on every push at $0 and never touches a quota. |
