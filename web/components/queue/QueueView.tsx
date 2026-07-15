"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import {
  ACTIONS,
  CUSTOMER_BY_ID,
  ESCALATION_THRESHOLD_USD,
  type ActionState,
  type QueueAction,
  type ToolName,
} from "@/lib/fixtures";
import { ago, usdShort } from "@/lib/format";
import { EffectBadge, StateBadge } from "../Effect";
import { IconChevron, IconFilter, IconInjection, IconSearch } from "../icons";
import styles from "./queue.module.css";

/**
 * The approval queue — the screen this console exists for.
 *
 * Design notes worth keeping:
 *  · Rows are 34px. An approver is scanning for the two that matter out of
 *    forty, so the whole set should fit on one screen without scrolling.
 *  · The deciding rule gets its own column. "Why is this held?" is the question
 *    every single row is asked, and making someone open the detail to answer it
 *    is the difference between a tool and a demo.
 *  · j/k/Enter work, because the people who use this all day will not reach for
 *    the mouse.
 *
 * Motion here is framer-motion, not GSAP: this component owns filter state and
 * re-renders constantly, which is precisely the situation `useGSAP` handles
 * badly (its cleanup reverts the context on every render).
 */

const STATES: { id: ActionState | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "escalated", label: "Escalated" },
];

const TOOLS: (ToolName | "all")[] = ["all", "issue_refund", "change_plan", "cancel"];

export function QueueView() {
  const router = useRouter();
  const params = useSearchParams();
  const customerFilter = params.get("customer");

  const [state, setState] = useState<ActionState | "all">("pending");
  const [tool, setTool] = useState<ToolName | "all">("all");
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const tableRef = useRef<HTMLTableSectionElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return ACTIONS.filter((a) => {
      if (state !== "all" && a.state !== state) return false;
      if (tool !== "all" && a.tool !== tool) return false;
      if (customerFilter && a.customerId !== customerFilter) return false;
      if (!needle) return true;
      const c = CUSTOMER_BY_ID[a.customerId];
      return `${a.id} ${a.tool} ${c?.name ?? ""} ${c?.company ?? ""} ${a.request}`
        .toLowerCase()
        .includes(needle);
    });
  }, [state, tool, q, customerFilter]);

  useEffect(() => {
    setCursor(0);
  }, [state, tool, q, customerFilter]);

  // j/k/Enter — the power-tool path.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => Math.min(c + 1, rows.length - 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (e.key === "Enter" && rows[cursor]) {
        e.preventDefault();
        router.push(`/queue/${rows[cursor].id}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, cursor, router]);

  useEffect(() => {
    tableRef.current?.querySelector('[data-cursor="true"]')?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const counts = useMemo(() => {
    const base: Record<string, number> = { all: ACTIONS.length };
    for (const s of STATES) if (s.id !== "all") base[s.id] = ACTIONS.filter((a) => a.state === s.id).length;
    return base;
  }, []);

  const cust = customerFilter ? CUSTOMER_BY_ID[customerFilter] : null;

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headMain}>
          <h1 className={styles.title}>Approval queue</h1>
          <p className={styles.sub}>
            Every action the agent proposed. The engine decided each one before it could run —
            anything worth more than{" "}
            <span className="tnum">${ESCALATION_THRESHOLD_USD.toFixed(2)}</span> waits for you.
          </p>
        </div>
        <div className={styles.headStats}>
          <Stat label="Awaiting you" value={counts.pending} tone="escalate" />
          <Stat label="Blocked by policy" value={counts.rejected} tone="deny" />
          <Stat label="Auto-executed" value={counts.approved} tone="allow" />
        </div>
      </header>

      {/* ── filters ─────────────────────────────────────────────────────── */}
      <div className={styles.controls}>
        <div className={styles.tabs} role="tablist" aria-label="Filter by state">
          {STATES.map((s) => (
            <button
              key={s.id}
              role="tab"
              aria-selected={state === s.id}
              className={styles.tab}
              data-active={state === s.id || undefined}
              onClick={() => setState(s.id)}
            >
              {s.label}
              <span className={`${styles.tabCount} tnum`}>{counts[s.id]}</span>
            </button>
          ))}
        </div>

        <div className={styles.controlsRight}>
          <label className={styles.select}>
            <IconFilter size={12} />
            <span className="sr-only">Filter by tool</span>
            <select value={tool} onChange={(e) => setTool(e.target.value as ToolName | "all")}>
              {TOOLS.map((t) => (
                <option key={t} value={t}>
                  {t === "all" ? "All tools" : t}
                </option>
              ))}
            </select>
          </label>

          <div className={styles.searchBox}>
            <IconSearch size={13} />
            <input
              ref={searchRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter rows…"
              aria-label="Filter rows"
              spellCheck={false}
            />
          </div>
        </div>
      </div>

      {cust && (
        <div className={styles.pill}>
          <span>
            Filtered to <strong>{cust.name}</strong> · {cust.company}
          </span>
          <button type="button" onClick={() => router.push("/queue")}>
            Clear
          </button>
        </div>
      )}

      {/* ── table ───────────────────────────────────────────────────────── */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className="sr-only">
            Proposed actions and their policy decisions. Use j and k to move, Enter to open.
          </caption>
          <thead>
            <tr>
              <th scope="col" className={styles.thState}>State</th>
              <th scope="col">Action</th>
              <th scope="col">Customer</th>
              <th scope="col">Proposed call</th>
              <th scope="col" className={styles.thNum}>Value</th>
              <th scope="col">Deciding rule</th>
              <th scope="col" className={styles.thAge}>Age</th>
              <th scope="col" aria-label="Open" />
            </tr>
          </thead>
          <tbody ref={tableRef}>
            {rows.map((a, i) => (
              <Row key={a.id} a={a} active={i === cursor} onHover={() => setCursor(i)} />
            ))}
          </tbody>
        </table>

        {rows.length === 0 && (
          <div className={styles.empty}>
            <p>No actions match this filter.</p>
            <span>
              {state === "pending"
                ? "Nothing is waiting on a human right now."
                : "Widen the filter or clear the search."}
            </span>
          </div>
        )}
      </div>

      <footer className={styles.foot}>
        <span className={styles.hint}>
          <kbd>j</kbd>
          <kbd>k</kbd> move · <kbd>↵</kbd> open · <kbd>/</kbd> search
        </span>
        <span className={styles.footCount}>
          <span className="tnum">{rows.length}</span> of{" "}
          <span className="tnum">{ACTIONS.length}</span> actions
        </span>
      </footer>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={styles.stat} data-tone={tone}>
      <span className={`${styles.statValue} tnum`}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}

function Row({ a, active, onHover }: { a: QueueAction; active: boolean; onHover: () => void }) {
  const router = useRouter();
  const c = CUSTOMER_BY_ID[a.customerId];
  const deciding = a.evaluated.find((o) => o.effect === "deny") ?? a.evaluated.find((o) => o.effect === "escalate");

  return (
    <motion.tr
      className={styles.row}
      data-cursor={active}
      data-effect={a.effect}
      onMouseMove={onHover}
      onClick={() => router.push(`/queue/${a.id}`)}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          router.push(`/queue/${a.id}`);
        }
      }}
    >
      <td className={styles.tdState}>
        <StateBadge state={a.state} size="sm" />
      </td>
      <td>
        <span className={styles.actionId}>
          {a.adversarial && (
            <span className={styles.injectFlag} title="Adversarial input — prompt injection">
              <IconInjection size={11} />
              <span className="sr-only">Adversarial input</span>
            </span>
          )}
          {a.id}
        </span>
      </td>
      <td className={styles.tdCust}>
        <span className={styles.custName}>{c?.name}</span>
        <span className={styles.custCo}>{c?.company}</span>
      </td>
      <td className={styles.tdCall}>
        <code className={styles.tool}>{a.tool}</code>
        <span className={styles.callArgs}>
          {a.tool === "issue_refund"
            ? String(a.args.charge_id)
            : a.tool === "change_plan"
              ? `→ ${String(a.args.target_plan)}${a.args.prorate ? " · prorated" : " · no proration"}`
              : `${String(a.args.subscription_id)}${a.args.at_period_end ? " · at period end" : " · immediate"}`}
        </span>
      </td>
      <td className={`${styles.tdNum} tnum`}>{usdShort(a.valueUsd)}</td>
      <td className={styles.tdRule}>
        {deciding ? (
          <span className={styles.ruleCell}>
            <EffectBadge effect={deciding.effect} size="sm" label={deciding.ruleId} />
          </span>
        ) : (
          <span className={styles.ruleNone}>no rule objected</span>
        )}
      </td>
      <td className={`${styles.tdAge} tnum`}>{ago(a.createdAt)}</td>
      <td className={styles.tdGo}>
        <IconChevron size={13} />
      </td>
    </motion.tr>
  );
}
