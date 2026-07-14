"use client";

import { useState } from "react";
import { SCENARIOS, strictest, type Effect, type RuleOutcome, type Scenario } from "@/lib/fixtures";

/* Icons are inline SVG: no icon dependency, and every state carries a SHAPE as well
   as a colour, so the meaning survives colour-blindness and greyscale printing. */

function IconDeny() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="8.25" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6.5 6.5l7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function IconEscalate() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M10 2.75l7.5 13H2.5l7.5-13z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M10 8v3.25" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="10" cy="13.75" r="0.85" fill="currentColor" />
    </svg>
  );
}

function IconAllow() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="8.25" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6.25 10.25l2.5 2.5 5-5.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function effectIcon(effect: Effect) {
  if (effect === "deny") return <IconDeny />;
  if (effect === "escalate") return <IconEscalate />;
  return <IconAllow />;
}

/** Label text is explicit — colour is never the only signal. */
function effectLabel(effect: Effect): string {
  if (effect === "deny") return "Blocked";
  if (effect === "escalate") return "Escalated";
  return "Allowed";
}

function pillClass(effect: Effect): string {
  if (effect === "deny") return "ds-pill ds-pill--rejected";
  if (effect === "escalate") return "ds-pill ds-pill--escalated";
  return "ds-pill ds-pill--approved";
}

function RuleRow({ outcome }: { outcome: RuleOutcome }) {
  const cls =
    outcome.effect === "deny" ? "rule rule--fired" : outcome.effect === "escalate" ? "rule rule--escalated" : "rule";
  return (
    <li className={cls}>
      <span style={{ color: `var(--state-${outcome.effect === "deny" ? "rejected" : outcome.effect === "escalate" ? "escalated" : "approved"}-accent)` }}>
        {effectIcon(outcome.effect)}
      </span>
      <div>
        <div className="rule-id">
          {outcome.ruleId} <span className={pillClass(outcome.effect)}>{effectLabel(outcome.effect)}</span>
        </div>
        <p className="rule-reason">{outcome.reason}</p>
      </div>
    </li>
  );
}

function StoppedState({ scenario }: { scenario: Scenario }) {
  const { decision } = scenario;
  const fired = scenario.outcomes.filter((o) => o.effect === decision);

  const heading =
    decision === "deny"
      ? "Action blocked by policy"
      : decision === "escalate"
        ? "Held for human approval"
        : "Action allowed";

  const body =
    decision === "deny"
      ? "The agent proposed this action. The policy engine refused it, and it was never executed."
      : decision === "escalate"
        ? "The agent proposed this action. It is valid, but exceeds a threshold that requires a human decision, so it is paused — and it stays paused across a restart."
        : "Every rule passed. The action executed and was written to the audit log.";

  return (
    <section className={`stopped stopped--${decision}`} aria-live="polite">
      <span aria-hidden="true">{effectIcon(decision)}</span>
      <div>
        <h2>{heading}</h2>
        <p>{body}</p>
        {fired.length > 0 && decision !== "allow" && (
          <p style={{ marginTop: "0.6rem" }}>
            <strong>
              {fired.length === 1 ? "Rule that fired:" : "Rules that fired:"}{" "}
              {fired.map((f) => f.ruleId).join(", ")}
            </strong>
          </p>
        )}
        {decision === "escalate" && (
          <div className="stopped-actions">
            <button className="ds-btn ds-btn--primary ds-btn--sm" type="button">
              Approve
            </button>
            <button className="ds-btn ds-btn--ghost ds-btn--sm" type="button">
              Reject
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

export default function Page() {
  const [selectedId, setSelectedId] = useState(SCENARIOS[0].id);
  const scenario = SCENARIOS.find((s) => s.id === selectedId) ?? SCENARIOS[0];

  // Recomputed here rather than trusted from the fixture — the same fold the
  // engine does (models.py :: strictest), so the UI can never disagree with it.
  const decision = strictest(scenario.outcomes.map((o) => o.effect));

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <h1>Policy-Guarded Ops Agent</h1>
          <p>
            An AI agent that issues refunds, changes plans and cancels subscriptions — and <strong>provably cannot</strong>{" "}
            break the business rules. The policy engine is deterministic code, not a prompt: the model only proposes, the
            rules decide.
          </p>
        </div>
        <span className="demo-tag">Demo build · fixtures · no API key</span>
      </header>

      <div className="workspace">
        <div>
          <h2 className="col-head">Incoming requests</h2>
          <ul className="queue">
            {SCENARIOS.map((s) => {
              const d = strictest(s.outcomes.map((o) => o.effect));
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    className="queue-item"
                    aria-current={s.id === selectedId}
                    onClick={() => setSelectedId(s.id)}
                  >
                    <span className="queue-item-top">
                      <span className="queue-item-who">{s.customer.name}</span>
                      <span className={pillClass(d)}>{effectLabel(d)}</span>
                    </span>
                    <p className="queue-item-req">{s.request}</p>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="detail">
          <StoppedState scenario={{ ...scenario, decision }} />

          <section className="panel">
            <h3>Customer request</h3>
            <blockquote className="quote">{scenario.request}</blockquote>
          </section>

          <section className="panel">
            <h3>Facts the rules may see</h3>
            <dl className="facts">
              {scenario.facts.map((f) => (
                <div key={f.label}>
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="panel">
            <h3>What the model proposed</h3>
            <div className="tool-call">
              {scenario.action.tool}({"\n"}
              {Object.entries(scenario.action.args).map(([k, v]) => (
                <span key={k}>
                  {"  "}
                  {k}={typeof v === "string" ? `"${v}"` : v},{"\n"}
                </span>
              ))}
              )
            </div>
            <p className="rationale">“{scenario.action.rationale}”</p>
          </section>

          <section className="panel">
            <h3>Policy engine — every rule, every verdict</h3>
            <ul className="rules">
              {scenario.outcomes.map((o) => (
                <RuleRow key={o.ruleId} outcome={o} />
              ))}
            </ul>
          </section>

          <section className="ablation">
            <h3>Ablation — the same request, policy engine off</h3>
            <div className="ablation-row">
              <div className="ablation-cell">
                <h4>Policy engine ON</h4>
                <p>
                  <strong>{effectLabel(decision)}.</strong>{" "}
                  {decision === "allow" ? "Executed and audited." : "Never executed."}
                </p>
              </div>
              <div className={`ablation-cell${decision === "allow" ? "" : " ablation-cell--off"}`}>
                <h4>Policy engine OFF</h4>
                <p>{scenario.withoutPolicy.note}</p>
              </div>
            </div>
          </section>
        </div>
      </div>

      <footer className="footnote">
        <p>
          This is a <strong>demo build</strong>, not client work. It runs on fixtures that mirror the real backend&apos;s
          rule ids, effects and severities exactly — no API key, no database. The policy engine, LangGraph state machine,
          audit trail and eval suite are in the repo.
        </p>
        <p>
          <a href="https://github.com/tmaslam/policy-guard">github.com/tmaslam/policy-guard</a>
        </p>
      </footer>
    </main>
  );
}
