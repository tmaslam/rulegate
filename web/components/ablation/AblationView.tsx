"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { ablationSummary, allOutcomes, armOutcome } from "@/lib/ablation";
import { ACTION_BY_ID, ACTIONS, CUSTOMER_BY_ID, HERO_ACTION_ID } from "@/lib/fixtures";
import { argLine, usd, usdShort } from "@/lib/format";
import { EffectBadge } from "../Effect";
import { IconAllow, IconDeny, IconInjection, IconTool } from "../icons";
import styles from "./ablation.module.css";

/**
 * Guard on / off — the whole pitch on one screen.
 *
 * `POLICY_ENABLED=False` is a real flag in the service. It swaps `evaluate()`
 * for `bypass()`, which returns ALLOW with an empty `evaluated` tuple, so the
 * record itself says nothing was checked. This page shows what that costs.
 *
 * HONESTY, because this is exactly the page where it would be tempting to lie:
 * there are no measurements here. The OFF column is not an observation of a run
 * — it is what the flag *would* let through, worked out by re-reading the same
 * rules against the same fixture actions. Every count says which dataset it
 * came from. There is no violation rate, no saving, no accuracy, no benchmark,
 * because none has been produced.
 */

const SCENARIOS = [HERO_ACTION_ID, "act_2H9T51", "act_8L6W27", "act_4R2B73", "act_7K5V16"];

export function AblationView() {
  const [scenarioId, setScenarioId] = useState(HERO_ACTION_ID);
  const [guard, setGuard] = useState(true);

  const summary = useMemo(() => ablationSummary(), []);
  const outcomes = useMemo(() => allOutcomes(), []);
  const action = ACTION_BY_ID[scenarioId];
  const outcome = useMemo(() => armOutcome(action), [action]);
  const customer = CUSTOMER_BY_ID[action.customerId];

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <h1 className={styles.title}>Guard on / off</h1>
          <p className={styles.sub}>
            <code>POLICY_ENABLED</code> is a real flag. Setting it false swaps the engine&rsquo;s{" "}
            <code>evaluate()</code> for <code>bypass()</code>, which returns an allow with an empty
            rule list — so the trail records, in the record itself, that nothing was checked. Here
            is what that changes.
          </p>
        </div>
      </header>

      {/* ── scenario picker ─────────────────────────────────────────────── */}
      <div className={styles.picker}>
        <span className={styles.pickerLabel}>Scenario</span>
        <div className={styles.pickerTabs} role="tablist" aria-label="Choose a scenario">
          {SCENARIOS.map((id) => {
            const a = ACTION_BY_ID[id];
            return (
              <button
                key={id}
                role="tab"
                aria-selected={scenarioId === id}
                className={styles.pickerTab}
                data-active={scenarioId === id || undefined}
                onClick={() => setScenarioId(id)}
              >
                {a.adversarial && <IconInjection size={11} />}
                <code>{a.id}</code>
                <span>{a.tool}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── the request ─────────────────────────────────────────────────── */}
      <section className={styles.request}>
        <div className={styles.requestHead}>
          <span className={styles.requestTag}>
            {action.adversarial ? "adversarial request" : "customer request"}
          </span>
          <span className={styles.requestMeta}>
            {customer?.name} · {customer?.company} · {action.channel}
          </span>
        </div>
        <p className={styles.requestText}>{action.request}</p>
        <div className={styles.requestCall}>
          <IconTool size={12} />
          <code>
            <span className={styles.callName}>{action.tool}</span>({argLine(action.args)})
          </code>
        </div>
      </section>

      {/* ── the switch ──────────────────────────────────────────────────── */}
      <div className={styles.switchRow}>
        <div className={styles.switchWrap}>
          <button
            type="button"
            role="switch"
            aria-checked={guard}
            className={styles.switch}
            data-on={guard || undefined}
            onClick={() => setGuard((g) => !g)}
          >
            <span className={styles.switchTrack}>
              <motion.span
                className={styles.switchThumb}
                layout
                transition={{ type: "spring", stiffness: 520, damping: 34 }}
              />
            </span>
            <span className={styles.switchText}>
              <code>policy_enabled</code>
              <strong>{guard ? "true" : "false"}</strong>
            </span>
          </button>
          <p className={styles.switchHint}>
            {guard
              ? "The engine evaluates every rule before the tool call is allowed to exist."
              : "The engine is bypassed. No rule runs. The agent's proposal goes straight to billing."}
          </p>
        </div>
      </div>

      {/* ── the two arms ────────────────────────────────────────────────── */}
      <div className={styles.arms}>
        {/* ON */}
        <section className={styles.arm} data-arm="on" data-dim={!guard || undefined}>
          <header className={styles.armHead}>
            <span className={styles.armLabel}>
              <span className={styles.armDot} data-on aria-hidden />
              Guard on
            </span>
            <code className={styles.armFlag}>policy_enabled=true</code>
          </header>

          <div className={styles.armBody}>
            <ol className={styles.checks}>
              {action.evaluated.map((o) => (
                <li key={o.ruleId} className={styles.check} data-effect={o.effect}>
                  <code>{o.ruleId}</code>
                  <EffectBadge effect={o.effect} size="sm" />
                </li>
              ))}
            </ol>

            <div className={styles.armVerdict} data-effect={outcome.on}>
              <div className={styles.armVerdictIcon}>
                {outcome.on === "deny" ? <IconDeny size={16} /> : outcome.on === "allow" ? <IconAllow size={16} /> : <IconTool size={16} />}
              </div>
              <div>
                <strong>
                  {outcome.on === "deny"
                    ? "Blocked — tool call never issued"
                    : outcome.on === "escalate"
                      ? "Held — waiting on a human"
                      : "Executed — no rule objected"}
                </strong>
                <span>
                  {outcome.on === "deny"
                    ? `${usd(action.valueUsd)} stayed where it was.`
                    : outcome.on === "escalate"
                      ? `${usd(action.valueUsd)} is not moving until someone signs it off.`
                      : "This one was legal on its own merits."}
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* OFF */}
        <section className={styles.arm} data-arm="off" data-dim={guard || undefined}>
          <header className={styles.armHead}>
            <span className={styles.armLabel}>
              <span className={styles.armDot} aria-hidden />
              Guard off
            </span>
            <code className={styles.armFlag}>policy_enabled=false</code>
          </header>

          <div className={styles.armBody}>
            <div className={styles.noChecks}>
              <span className={styles.noChecksLine} aria-hidden />
              <span className={styles.noChecksText}>
                no rules evaluated · <code>evaluated=()</code>
              </span>
              <span className={styles.noChecksLine} aria-hidden />
            </div>

            <div className={styles.armVerdict} data-effect={outcome.offViolations.length ? "deny" : "allow"}>
              <div className={styles.armVerdictIcon}>
                {outcome.offViolations.length ? <IconInjection size={16} /> : <IconAllow size={16} />}
              </div>
              <div>
                <strong>
                  {outcome.offViolations.length ? "Executed anyway" : "Executed — and it was fine"}
                </strong>
                <span>
                  {outcome.offViolations.length
                    ? `${usd(action.valueUsd)} left the business. The detector recorded ${outcome.offViolations.length} violation${outcome.offViolations.length > 1 ? "s" : ""} after the fact.`
                    : "No rule would have objected to this one. The guard is not what made it safe."}
                </span>
              </div>
            </div>

            {outcome.offViolations.length > 0 && (
              <ul className={styles.violations}>
                {outcome.offViolations.map((v) => (
                  <li key={v.ruleId} className={styles.violation} data-sev={v.severity}>
                    <span className={styles.violationTop}>
                      <code>{v.ruleId}</code>
                      <span className={styles.sev} data-sev={v.severity}>
                        {v.severity}
                      </span>
                    </span>
                    <span className={styles.violationText}>{v.rationale}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      {/* ── the point ───────────────────────────────────────────────────── */}
      <AnimatePresence mode="wait">
        <motion.aside
          key={guard ? "on" : "off"}
          className={styles.point}
          data-on={guard || undefined}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
        >
          {guard ? (
            <p>
              <strong>The model was persuaded and it did not matter.</strong> It proposed the refund;
              the rules are not reachable from the prompt. A deny here is not the system failing —
              it is the only part of the system that was ever going to hold.
            </p>
          ) : (
            <p>
              <strong>Nothing stopped it.</strong> The violation was still detected — that is what
              makes the two arms comparable — but detection happens after the money is gone. The
              flag exists to measure what the guard is worth, and it is never set false in
              production.
            </p>
          )}
        </motion.aside>
      </AnimatePresence>

      {/* ── the full set ────────────────────────────────────────────────── */}
      <section className={styles.tableSection}>
        <div className={styles.tableHead}>
          <h2 className={styles.h2}>Every action in this dataset, both ways</h2>
          <p className={styles.tableSub}>
            Derived by running the same rules over the same{" "}
            <span className="tnum">{ACTIONS.length}</span> fixture actions — arithmetic over the
            data on this page, not a measurement of a live system.
          </p>
        </div>

        <div className={styles.summary}>
          <SummaryCard
            label="Executed with the guard on"
            value={summary.onExecuted}
            total={summary.total}
            tone="allow"
          />
          <SummaryCard
            label="Held for a human"
            value={summary.onHeld}
            total={summary.total}
            tone="escalate"
          />
          <SummaryCard
            label="Blocked outright"
            value={summary.onBlocked}
            total={summary.total}
            tone="deny"
          />
          <SummaryCard
            label="Critical violations if bypassed"
            value={summary.offCritical}
            total={summary.total}
            tone="deny"
            note="a rule would have denied it"
          />
          <SummaryCard
            label="High violations if bypassed"
            value={summary.offHigh}
            total={summary.total}
            tone="escalate"
            note="executed without required approval"
          />
        </div>

        <p className={styles.moneyNote}>
          Refund requests the engine did not release in this fixture set:{" "}
          <strong className="tnum">{usd(summary.refundDollarsHeld)}</strong>. That is the sum of the
          refund amounts on the rows below that were denied or held — a fact about these{" "}
          <span className="tnum">{ACTIONS.length}</span> rows, not a projection, a saving, or a
          claim about anybody&rsquo;s production traffic.
        </p>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="sr-only">
              Each action with its guard-on decision and its guard-off outcome.
            </caption>
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Customer</th>
                <th scope="col">Call</th>
                <th scope="col" className={styles.num}>Value</th>
                <th scope="col">Guard on</th>
                <th scope="col">Guard off</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map((o) => {
                const c = CUSTOMER_BY_ID[o.action.customerId];
                const worst = o.offViolations.some((v) => v.severity === "critical")
                  ? "critical"
                  : o.offViolations.length
                    ? "high"
                    : "clean";
                return (
                  <tr key={o.action.id} className={styles.row}>
                    <td>
                      <Link href={`/queue/${o.action.id}`} className={styles.idLink}>
                        {o.action.adversarial && <IconInjection size={10} />}
                        {o.action.id}
                      </Link>
                    </td>
                    <td className={styles.cust}>{c?.company}</td>
                    <td>
                      <code className={styles.tool}>{o.action.tool}</code>
                    </td>
                    <td className={`${styles.num} tnum`}>{usdShort(o.action.valueUsd)}</td>
                    <td>
                      <EffectBadge effect={o.on} size="sm" />
                    </td>
                    <td>
                      <span className={styles.offCell} data-worst={worst}>
                        {worst === "clean" ? (
                          <>
                            <IconAllow size={11} />
                            executed · no violation
                          </>
                        ) : (
                          <>
                            <IconInjection size={11} />
                            executed · {worst}
                          </>
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <footer className={styles.foot}>
        <p>
          Runs against a seeded billing dataset, so the comparison is reproducible — the same request
          hits the same rules every time. The rule logic is{" "}
          <code>src/policy_guarded_ops_agent/policy/</code>, evaluated exactly as the service evaluates
          it. Nothing here claims a measurement of this system&rsquo;s quality.{" "}
          <Link href="/rules">Read the rules</Link> ·{" "}
          <a href="https://github.com/tmaslam/rulegate" target="_blank" rel="noreferrer noopener">
            github.com/tmaslam/rulegate
          </a>
        </p>
      </footer>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  total,
  tone,
  note,
}: {
  label: string;
  value: number;
  total: number;
  tone: string;
  note?: string;
}) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className={styles.summaryCard} data-tone={tone}>
      <div className={styles.summaryTop}>
        <span className={`${styles.summaryValue} tnum`}>{value}</span>
        <span className={`${styles.summaryOf} tnum`}>/ {total}</span>
      </div>
      <span className={styles.summaryLabel}>{label}</span>
      {note && <span className={styles.summaryNote}>{note}</span>}
      {/* The bar is a proportion of the rows above it, nothing more. */}
      <span className={styles.meter} aria-hidden>
        <span className={styles.meterFill} style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}
