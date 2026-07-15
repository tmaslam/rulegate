"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ACTIONS, RULES, type Effect, type QueueAction, type RuleId } from "@/lib/fixtures";
import s from "./gatesim.module.css";

/**
 * The live policy gate.
 *
 * The whole product is one idea — the model proposes, code decides — and that is
 * far easier to watch than to read. So this replays a real scenario through the
 * real evaluation order: the request arrives, the model proposes a tool call,
 * then each rule that applied returns its verdict one at a time, with the actual
 * rationale the engine produced.
 *
 * Nothing here is invented for the page. Every scenario, rule outcome and
 * rationale is read from `lib/fixtures.ts`, which mirrors what the console runs.
 */

const RULE_ORDER: RuleId[] = [
  "entity-must-exist",
  "refund-window-30d",
  "refund-within-balance",
  "downgrade-requires-proration",
  "high-value-escalation",
];

const RULE_TITLE = new Map(RULES.map((r) => [r.id, r.title]));

/** Four scenarios that between them show every verdict the engine can reach. */
function pickScenarios(): QueueAction[] {
  const injection = ACTIONS.find((a) => a.adversarial);
  const lateRefund = ACTIONS.find(
    (a) => !a.adversarial && a.effect === "deny" && a.evaluated.some((e) => e.ruleId === "refund-window-30d" && e.effect === "deny"),
  );
  const escalate = ACTIONS.find((a) => !a.adversarial && a.effect === "escalate");
  const allow = ACTIONS.find((a) => a.effect === "allow");
  return [injection, lateRefund, escalate, allow].filter((a): a is QueueAction => Boolean(a));
}

const LABEL: Record<string, string> = {
  deny: "Denied",
  escalate: "Escalated",
  allow: "Allowed",
};

function Tick() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function Cross() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}
function Bang() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M8 3.5v5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="8" cy="12" r="1.1" fill="currentColor" />
    </svg>
  );
}

function EffectIcon({ effect }: { effect: Effect }) {
  if (effect === "allow") return <Tick />;
  if (effect === "deny") return <Cross />;
  return <Bang />;
}

type Phase = "idle" | "request" | "model" | "rules" | "verdict";

export default function GateSim() {
  const scenarios = useMemo(pickScenarios, []);
  const [idx, setIdx] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [shown, setShown] = useState(0);
  const timers = useRef<number[]>([]);

  const action = scenarios[idx];

  // Only the rules that actually applied, in the engine's evaluation order.
  const outcomes = useMemo(() => {
    if (!action) return [];
    const byId = new Map(action.evaluated.map((e) => [e.ruleId, e]));
    return RULE_ORDER.map((id) => byId.get(id)).filter((e): e is NonNullable<typeof e> => Boolean(e));
  }, [action]);

  const clear = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  }, []);

  const run = useCallback(() => {
    clear();
    setPhase("request");
    setShown(0);

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setShown(outcomes.length);
      setPhase("verdict");
      return;
    }

    const at = (ms: number, fn: () => void) => timers.current.push(window.setTimeout(fn, ms));

    at(550, () => setPhase("model"));
    at(1500, () => setPhase("rules"));
    outcomes.forEach((_, i) => at(1500 + (i + 1) * 620, () => setShown(i + 1)));
    at(1500 + (outcomes.length + 1) * 620, () => setPhase("verdict"));
  }, [outcomes, clear]);

  // Replay whenever the scenario changes, including on first mount.
  useEffect(() => {
    run();
    return clear;
  }, [run, clear]);

  if (!action) return null;

  const verdictShown = phase === "verdict";

  return (
    <div className={s.wrap}>
      <div className={s.chips} role="tablist" aria-label="Scenario">
        {scenarios.map((a, i) => (
          <button
            key={a.id}
            role="tab"
            aria-selected={i === idx}
            className={`${s.chip} ${i === idx ? s.chipOn : ""}`}
            onClick={() => setIdx(i)}
          >
            {a.adversarial ? "Prompt injection" : a.effect === "deny" ? "Late refund" : a.effect === "escalate" ? "High value" : "Legitimate"}
          </button>
        ))}
        <button className={s.replay} onClick={run} aria-label="Replay">
          Replay
        </button>
      </div>

      <div className={s.board}>
        {/* 1 — what the customer sent */}
        <div className={`${s.lane} ${phase !== "idle" ? s.in : ""}`}>
          <span className={s.laneTag}>Customer · {action.channel}</span>
          <p className={s.request}>{action.request}</p>
        </div>

        <div className={s.flow} aria-hidden="true">
          <span className={`${s.flowDot} ${phase !== "idle" && phase !== "request" ? s.flowOn : ""}`} />
        </div>

        {/* 2 — what the model proposed */}
        <div className={`${s.lane} ${s.modelLane} ${phase === "model" || phase === "rules" || verdictShown ? s.in : s.out}`}>
          <span className={s.laneTag}>The model proposes</span>
          <code className={s.tool}>
            {action.tool}({Object.entries(action.args).map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : v}`).join(", ")})
          </code>
          <p className={s.reasoning}>&ldquo;{action.reasoning}&rdquo;</p>
          <span className={s.noAuthority}>The model&apos;s reasoning is recorded. It is never authoritative.</span>
        </div>

        <div className={s.flow} aria-hidden="true">
          <span className={`${s.flowDot} ${phase === "rules" || verdictShown ? s.flowOn : ""}`} />
        </div>

        {/* 3 — the gate */}
        <div className={`${s.lane} ${s.gate} ${phase === "rules" || verdictShown ? s.in : s.out}`}>
          <span className={s.laneTag}>policy/engine.py · deterministic</span>
          <ul className={s.rules}>
            {outcomes.map((o, i) => (
              <li key={o.ruleId} className={`${s.rule} ${i < shown ? s.ruleIn : ""} ${s[`r_${o.effect}`]}`}>
                <span className={s.ruleIcon}>
                  <EffectIcon effect={o.effect} />
                </span>
                <span className={s.ruleBody}>
                  <code className={s.ruleId}>{o.ruleId}</code>
                  <span className={s.ruleTitle}>{RULE_TITLE.get(o.ruleId)}</span>
                  <span className={s.ruleWhy}>{o.rationale}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className={s.flow} aria-hidden="true">
          <span className={`${s.flowDot} ${verdictShown ? s.flowOn : ""}`} />
        </div>

        {/* 4 — the verdict */}
        <div className={`${s.verdict} ${s[`v_${action.effect}`]} ${verdictShown ? s.in : s.out}`} aria-live="polite">
          <span className={s.verdictIcon}>
            <EffectIcon effect={action.effect} />
          </span>
          <div>
            <strong className={s.verdictWord}>{LABEL[action.effect]}</strong>
            <p className={s.verdictWhy}>
              {action.effect === "allow"
                ? "Every rule allowed it. The action executed."
                : `Strictest verdict wins — ${outcomes.filter((o) => o.effect === action.effect).map((o) => o.ruleId).join(", ")}.`}
              {action.valueUsd !== null && <> Value at stake: ${action.valueUsd.toLocaleString()}.</>}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
