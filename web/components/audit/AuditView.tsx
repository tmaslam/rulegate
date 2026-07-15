"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AUDIT, RULES, type AuditEvent } from "@/lib/fixtures";
import { stamp } from "@/lib/format";
import { EffectGlyph } from "../Effect";
import { IconChevron, IconSearch, IconTool, IconUser } from "../icons";
import styles from "./audit.module.css";

/**
 * The audit trail.
 *
 * Built from the same fixture actions the queue renders, so the two can never
 * drift — a trail assembled by hand alongside the thing it describes is a trail
 * that lies eventually.
 *
 * Every row is one recorded fact: an inbound request, a tool call, one rule's
 * verdict, the folded decision, an execution (or a block), or a human acting.
 * A `policy_check` row is the unit an auditor actually cares about, which is
 * why each rule gets its own line rather than being summarised into a decision.
 */

const KINDS: { id: AuditEvent["kind"] | "all"; label: string }[] = [
  { id: "all", label: "All events" },
  { id: "request", label: "Requests" },
  { id: "tool_call", label: "Tool calls" },
  { id: "policy_check", label: "Policy checks" },
  { id: "decision", label: "Decisions" },
  { id: "execution", label: "Executions" },
  { id: "human", label: "Human actions" },
];

const KIND_LABEL: Record<AuditEvent["kind"], string> = {
  request: "request",
  tool_call: "tool_call",
  policy_check: "policy_check",
  decision: "decision",
  execution: "execution",
  human: "human",
};

export function AuditView() {
  const [kind, setKind] = useState<AuditEvent["kind"] | "all">("all");
  const [rule, setRule] = useState<string>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return AUDIT.filter((e) => {
      if (kind !== "all" && e.kind !== kind) return false;
      if (rule !== "all" && e.ruleId !== rule) return false;
      if (!needle) return true;
      return `${e.actionId} ${e.actor} ${e.summary} ${e.detail ?? ""}`.toLowerCase().includes(needle);
    });
  }, [kind, rule, q]);

  const counts = useMemo(() => {
    const base: Record<string, number> = { all: AUDIT.length };
    for (const k of KINDS) if (k.id !== "all") base[k.id] = AUDIT.filter((e) => e.kind === k.id).length;
    return base;
  }, []);

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <h1 className={styles.title}>Audit trail</h1>
          <p className={styles.sub}>
            Every request, tool call, rule verdict, decision and human action — in order, with the
            evidence each rule saw. Nothing is summarised away.
          </p>
        </div>
        <div className={styles.headCount}>
          <span className={`${styles.headCountVal} tnum`}>{AUDIT.length}</span>
          <span className={styles.headCountLabel}>events recorded</span>
        </div>
      </header>

      <div className={styles.controls}>
        <div className={styles.tabs} role="tablist" aria-label="Filter by event kind">
          {KINDS.map((k) => (
            <button
              key={k.id}
              role="tab"
              aria-selected={kind === k.id}
              className={styles.tab}
              data-active={kind === k.id || undefined}
              onClick={() => setKind(k.id)}
            >
              {k.label}
              <span className={`${styles.tabCount} tnum`}>{counts[k.id]}</span>
            </button>
          ))}
        </div>

        <div className={styles.controlsRight}>
          <label className={styles.select}>
            <span className="sr-only">Filter by rule</span>
            <select value={rule} onChange={(e) => setRule(e.target.value)}>
              <option value="all">All rules</option>
              {RULES.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.searchBox}>
            <IconSearch size={13} />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter the trail…"
              aria-label="Filter the trail"
              spellCheck={false}
            />
          </div>
        </div>
      </div>

      <div className={styles.logWrap}>
        <div className={styles.logHead} aria-hidden>
          <span>Time (UTC)</span>
          <span>Kind</span>
          <span>Action</span>
          <span>Actor</span>
          <span>Event</span>
          <span />
        </div>

        <ul className={styles.log}>
          {rows.map((e) => {
            const isOpen = open === e.id;
            return (
              <li key={e.id} className={styles.entry} data-kind={e.kind} data-effect={e.effect}>
                <button
                  type="button"
                  className={styles.entryRow}
                  onClick={() => setOpen(isOpen ? null : e.id)}
                  aria-expanded={isOpen}
                >
                  <span className={`${styles.ts} tnum`}>{stamp(e.ts)}</span>

                  <span className={styles.kind}>
                    <span className={styles.kindDot} data-kind={e.kind} aria-hidden />
                    {KIND_LABEL[e.kind]}
                  </span>

                  <span className={styles.actionRef}>{e.actionId}</span>

                  <span className={styles.actor}>
                    {e.kind === "human" ? (
                      <IconUser size={11} />
                    ) : e.kind === "tool_call" ? (
                      <IconTool size={11} />
                    ) : null}
                    {e.actor}
                  </span>

                  <span className={styles.summary}>
                    {e.effect && <EffectGlyph effect={e.effect} />}
                    <span className={styles.summaryText}>{e.summary}</span>
                  </span>

                  <span className={styles.chev} data-open={isOpen || undefined}>
                    <IconChevron size={12} />
                  </span>
                </button>

                {isOpen && (
                  <div className={styles.detail}>
                    <div className={styles.detailBody}>
                      <span className={styles.detailLabel}>detail</span>
                      <p className={styles.detailText}>{e.detail ?? "—"}</p>
                    </div>
                    <div className={styles.detailMeta}>
                      {e.ruleId && (
                        <Link href={`/rules#${e.ruleId}`} className={styles.metaLink}>
                          rule: <code>{e.ruleId}</code>
                        </Link>
                      )}
                      {e.tool && (
                        <span className={styles.metaTag}>
                          tool: <code>{e.tool}</code>
                        </span>
                      )}
                      <Link href={`/queue/${e.actionId}`} className={styles.metaLink}>
                        open action →
                      </Link>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {rows.length === 0 && (
          <div className={styles.empty}>
            <p>No events match this filter.</p>
            <span>Clear the search or widen the kind filter.</span>
          </div>
        )}
      </div>

      <footer className={styles.foot}>
        <span>
          Showing <span className="tnum">{rows.length}</span> of{" "}
          <span className="tnum">{AUDIT.length}</span> events
        </span>
        <span className={styles.footNote}>Click any row for its detail and evidence.</span>
      </footer>
    </div>
  );
}
