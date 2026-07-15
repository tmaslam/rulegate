"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import {
  ACTIONS,
  ACTION_BY_ID,
  CHARGE_BY_ID,
  CUSTOMER_BY_ID,
  ESCALATION_THRESHOLD_USD,
  PLAN_MONTHLY_USD,
  REFUND_WINDOW_DAYS,
  SUBSCRIPTION_BY_ID,
  type ActionState,
} from "@/lib/fixtures";
import { argLine, dateOnly, stamp, usd } from "@/lib/format";
import { EffectBadge, StateBadge } from "../Effect";
import {
  IconAllow,
  IconArrowLeft,
  IconDeny,
  IconInjection,
  IconTool,
  IconUser,
} from "../icons";
import styles from "./detail.module.css";

/**
 * One action, in full.
 *
 * The order on this page is the order the service actually works in, and that
 * is the argument: request → facts resolved by tool calls → the model's
 * proposal → every rule's verdict → the fold. A reviewer should be able to
 * check the engine's work without trusting it, which means showing the evidence
 * each rule saw, not just its conclusion.
 */
export function ActionDetail({ id }: { id: string }) {
  const action = ACTION_BY_ID[id];
  const [override, setOverride] = useState<ActionState | null>(null);
  const [note, setNote] = useState("");
  const [toast, setToast] = useState<string | null>(null);

  const neighbours = useMemo(() => {
    const i = ACTIONS.findIndex((a) => a.id === id);
    return { prev: ACTIONS[i - 1], next: ACTIONS[i + 1] };
  }, [id]);

  if (!action) {
    return (
      <div className={styles.missing}>
        <h1>Action not found</h1>
        <p>
          No action with id <code>{id}</code> exists in this dataset.
        </p>
        <Link href="/queue" className={styles.backLink}>
          <IconArrowLeft size={13} /> Back to the queue
        </Link>
      </div>
    );
  }

  const customer = CUSTOMER_BY_ID[action.customerId];
  const charge = action.args.charge_id ? CHARGE_BY_ID[String(action.args.charge_id)] : undefined;
  const sub = action.args.subscription_id ? SUBSCRIPTION_BY_ID[String(action.args.subscription_id)] : undefined;
  const state = override ?? action.state;
  const decidable = action.effect === "escalate" && action.state === "pending";

  const act = (next: ActionState, verb: string) => {
    setOverride(next);
    setToast(`${action.id} ${verb}. Recorded in the audit trail against your name.`);
    window.setTimeout(() => setToast(null), 3600);
  };

  return (
    <div className={styles.wrap}>
      {/* ── breadcrumb ─────────────────────────────────────────────────── */}
      <div className={styles.crumbs}>
        <Link href="/queue" className={styles.backLink}>
          <IconArrowLeft size={13} />
          Queue
        </Link>
        <span className={styles.crumbSep}>/</span>
        <code className={styles.crumbId}>{action.id}</code>
        <div className={styles.crumbNav}>
          {neighbours.prev && (
            <Link href={`/queue/${neighbours.prev.id}`} className={styles.navBtn}>
              ← Prev
            </Link>
          )}
          {neighbours.next && (
            <Link href={`/queue/${neighbours.next.id}`} className={styles.navBtn}>
              Next →
            </Link>
          )}
        </div>
      </div>

      {/* ── verdict banner ─────────────────────────────────────────────── */}
      <section className={styles.banner} data-effect={action.effect}>
        <div className={styles.bannerIcon}>
          {action.effect === "deny" ? <IconDeny size={18} /> : action.effect === "allow" ? <IconAllow size={18} /> : <IconTool size={18} />}
        </div>
        <div className={styles.bannerBody}>
          <div className={styles.bannerTop}>
            <h1 className={styles.bannerTitle}>
              {action.effect === "deny"
                ? "Blocked by policy"
                : action.effect === "escalate"
                  ? "Held for human approval"
                  : "Auto-approved and executed"}
            </h1>
            <StateBadge state={state} />
            {action.adversarial && (
              <span className={styles.adversarial}>
                <IconInjection size={11} />
                adversarial input
              </span>
            )}
          </div>
          <p className={styles.bannerText}>
            {action.effect === "deny"
              ? "The strictest verdict wins. The tool call was never issued — no money moved and no state changed."
              : action.effect === "escalate"
                ? "Every rule was satisfied except the value threshold. The agent cannot resume this on its own."
                : "No rule objected, so the agent executed it without waiting for anyone."}
          </p>
        </div>
        <div className={styles.bannerValue}>
          <span className={`${styles.bannerAmount} tnum`} data-struck={action.effect === "deny" || undefined}>
            {usd(action.valueUsd)}
          </span>
          <span className={styles.bannerAmountLabel}>
            {action.effect === "deny" ? "not moved" : action.effect === "escalate" ? "pending" : "executed"}
          </span>
        </div>
      </section>

      <div className={styles.grid}>
        {/* ── main column ──────────────────────────────────────────────── */}
        <div className={styles.main}>
          {/* 1. request */}
          <Panel title="Customer request" meta={`${action.channel} · ${stamp(action.createdAt)}`}>
            <blockquote className={styles.request} data-adversarial={action.adversarial || undefined}>
              {action.request}
            </blockquote>
            {action.adversarial && (
              <p className={styles.injectNote}>
                <IconInjection size={12} />
                This message tries to talk the agent out of its own guardrails. It is recorded
                verbatim and treated as untrusted input — the rules below never read it.
              </p>
            )}
          </Panel>

          {/* 2. facts */}
          <Panel title="Facts the rules saw" meta="PolicyContext — resolved before evaluation">
            <p className={styles.panelIntro}>
              Rules are pure functions of these values. They perform no lookups of their own, so
              the same facts always produce the same verdict.
            </p>
            <dl className={styles.facts}>
              <Fact k="now" v={stamp("2026-07-14T09:14:00Z")} mono />
              <Fact k="customer" v={`${customer?.id} · ${customer?.tier}`} mono />
              {charge && (
                <>
                  <Fact k="charge.id" v={charge.id} mono />
                  <Fact k="charge.amount_usd" v={usd(charge.amountUsd)} mono />
                  <Fact k="charge.refunded_usd" v={usd(charge.refundedUsd)} mono />
                  <Fact k="charge.refundable_usd" v={usd(charge.amountUsd - charge.refundedUsd)} mono highlight />
                  <Fact k="charge.charged_at" v={stamp(charge.chargedAt)} mono />
                </>
              )}
              {sub && (
                <>
                  <Fact k="subscription.id" v={sub.id} mono />
                  <Fact k="subscription.plan" v={`${sub.plan} · ${usd(PLAN_MONTHLY_USD[sub.plan])}/mo`} mono />
                  <Fact k="subscription.status" v={sub.status} mono />
                  <Fact k="period_start" v={dateOnly(sub.periodStart)} mono />
                  <Fact k="period_end" v={dateOnly(sub.periodEnd)} mono />
                </>
              )}
              {!charge && !sub && (
                <Fact k="entity" v="unresolved — the referenced id does not exist" mono highlight />
              )}
            </dl>
          </Panel>

          {/* 3. proposal */}
          <Panel title="Proposed tool call" meta="what the model asked to do">
            <div className={styles.callBox}>
              <code className={styles.call}>
                <span className={styles.callName}>{action.tool}</span>(
                <span className={styles.callArgs}>{argLine(action.args)}</span>)
              </code>
            </div>
            <div className={styles.reasoning}>
              <span className={styles.reasoningLabel}>model reasoning</span>
              <p>{action.reasoning}</p>
              <span className={styles.reasoningFoot}>
                Recorded and shown, never authoritative. No rule reads this field.
              </span>
            </div>
          </Panel>

          {/* 4. the trace */}
          <Panel
            title="Rule trace"
            meta={`${action.evaluated.length} rules applied · strictest wins`}
          >
            <ol className={styles.trace}>
              {action.evaluated.map((o) => (
                <li key={o.ruleId} className={styles.traceItem} data-effect={o.effect}>
                  <div className={styles.traceHead}>
                    <code className={styles.traceRule}>{o.ruleId}</code>
                    <EffectBadge effect={o.effect} size="sm" />
                  </div>
                  <p className={styles.traceReason}>{o.rationale}</p>
                  <div className={styles.evidence}>
                    {Object.entries(o.evidence).map(([k, v]) => (
                      <span key={k} className={styles.evidenceItem}>
                        <span className={styles.evidenceKey}>{k}</span>
                        <span className={styles.evidenceVal}>{v}</span>
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ol>

            <div className={styles.fold} data-effect={action.effect}>
              <span className={styles.foldLabel}>fold</span>
              <span className={styles.foldText}>
                {action.evaluated.map((o) => o.effect).join(" · ")} →
              </span>
              <EffectBadge effect={action.effect} />
            </div>
          </Panel>
        </div>

        {/* ── sidebar ──────────────────────────────────────────────────── */}
        <aside className={styles.side}>
          {/* controls */}
          <div className={styles.card}>
            <h2 className={styles.cardTitle}>Decision</h2>
            {decidable ? (
              <>
                <p className={styles.cardText}>
                  This action is worth more than{" "}
                  <span className="tnum">${ESCALATION_THRESHOLD_USD.toFixed(2)}</span>, so{" "}
                  <code>high-value-escalation</code> withheld it. Your call.
                </p>
                <label className={styles.noteLabel} htmlFor="note">
                  Note for the trail
                </label>
                <textarea
                  id="note"
                  className={styles.note}
                  rows={3}
                  placeholder="Why are you approving or rejecting this?"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
                <div className={styles.actions}>
                  <button type="button" className={styles.approve} onClick={() => act("approved", "approved")}>
                    <IconAllow size={13} />
                    Approve
                  </button>
                  <button type="button" className={styles.reject} onClick={() => act("rejected", "rejected")}>
                    <IconDeny size={13} />
                    Reject
                  </button>
                </div>
                <button type="button" className={styles.escalateBtn} onClick={() => act("escalated", "escalated to the billing lead")}>
                  Escalate to billing lead
                </button>
              </>
            ) : (
              <div className={styles.settled}>
                <StateBadge state={state} />
                <p className={styles.cardText}>
                  {action.effect === "deny"
                    ? "A rule denied this. It is not an approval decision — there is no button that overrides a policy refusal, and that is the point."
                    : action.reviewedBy === "auto"
                      ? "No rule objected, so the engine executed it automatically. Nothing was withheld."
                      : "Already decided by a human."}
                </p>
                {action.reviewedBy && action.reviewedBy !== "auto" && (
                  <div className={styles.reviewer}>
                    <IconUser size={12} />
                    <div>
                      <strong>{action.reviewedBy}</strong>
                      <span>{action.reviewedAt ? stamp(action.reviewedAt) : ""}</span>
                    </div>
                  </div>
                )}
                {action.reviewNote && <p className={styles.reviewNote}>&ldquo;{action.reviewNote}&rdquo;</p>}
              </div>
            )}
          </div>

          {/* customer */}
          <div className={styles.card}>
            <h2 className={styles.cardTitle}>Customer</h2>
            <div className={styles.cust}>
              <span className={styles.custAvatar} aria-hidden>
                {customer?.name.split(" ").slice(0, 2).map((w) => w[0]).join("")}
              </span>
              <div>
                <strong>{customer?.name}</strong>
                <span>{customer?.company}</span>
              </div>
            </div>
            <dl className={styles.custFacts}>
              <Fact k="id" v={customer?.id ?? "—"} mono />
              <Fact k="email" v={customer?.email ?? "—"} mono />
              <Fact k="tier" v={customer?.tier ?? "—"} mono />
              <Fact k="plan" v={customer?.plan ?? "—"} mono />
              <Fact k="since" v={customer ? dateOnly(customer.since) : "—"} mono />
            </dl>
            <Link href={`/queue?customer=${customer?.id}`} className={styles.custLink}>
              All actions for this customer →
            </Link>
          </div>

          {/* thresholds */}
          <div className={styles.card}>
            <h2 className={styles.cardTitle}>Policy in force</h2>
            <dl className={styles.custFacts}>
              <Fact k="refund_window_days" v={String(REFUND_WINDOW_DAYS)} mono />
              <Fact k="escalation_threshold" v={usd(ESCALATION_THRESHOLD_USD)} mono />
              <Fact k="policy_enabled" v="true" mono highlight />
            </dl>
            <Link href="/rules" className={styles.custLink}>
              Read the rules →
            </Link>
          </div>
        </aside>
      </div>

      {/* ── toast ──────────────────────────────────────────────────────── */}
      <AnimatePresence>
        {toast && (
          <motion.div
            className={styles.toast}
            role="status"
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.99 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            <IconAllow size={14} />
            <span>{toast}</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Panel({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={styles.panel}>
      <header className={styles.panelHead}>
        <h2>{title}</h2>
        {meta && <span className={styles.panelMeta}>{meta}</span>}
      </header>
      <div className={styles.panelBody}>{children}</div>
    </section>
  );
}

function Fact({
  k,
  v,
  mono,
  highlight,
}: {
  k: string;
  v: string;
  mono?: boolean;
  highlight?: boolean;
}) {
  return (
    <div className={styles.fact} data-highlight={highlight || undefined}>
      <dt>{k}</dt>
      <dd className={mono ? "mono" : undefined}>{v}</dd>
    </div>
  );
}
