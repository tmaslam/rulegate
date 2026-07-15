"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ACTIONS,
  ENGINE_FOLD,
  ESCALATION_THRESHOLD_USD,
  REFUND_WINDOW_DAYS,
  RULES,
  ruleStats,
} from "@/lib/fixtures";
import { EffectBadge } from "../Effect";
import { IconChevron, IconGithub } from "../icons";
import { Code } from "./Code";
import styles from "./rules.module.css";

/**
 * The rules, as the code that runs.
 *
 * Showing the source is the argument, not decoration. "The agent obeys your
 * business rules" is a claim anyone can make about a system prompt; it only
 * means something if you can read the rule, see that it is arithmetic over
 * typed facts, and satisfy yourself that no amount of persuasion reaches it.
 *
 * ON THE COUNTS: `fired` and `applied` are computed from the fixture actions on
 * this deployment — they count rows in a dataset, and they are labelled as
 * such. They are not a measurement of how the rule performs in production, and
 * nothing on this page claims a hit rate, an accuracy or a saving.
 */
export function RulesView() {
  const [open, setOpen] = useState<string | null>(RULES[1].id);
  const stats = useMemo(() => ruleStats(), []);

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <h1 className={styles.title}>Policy rules</h1>
          <p className={styles.sub}>
            Five deterministic rules. Each is a pure function of the action and the facts — no I/O,
            no clock read, no model call. Given the same inputs they return the same verdict on
            every machine, forever.
          </p>
        </div>
        <a
          className={styles.repo}
          href="https://github.com/tmaslam/rulegate"
          target="_blank"
          rel="noreferrer noopener"
        >
          <IconGithub size={13} />
          policy/rules.py
        </a>
      </header>

      {/* ── config in force ─────────────────────────────────────────────── */}
      <div className={styles.config}>
        <ConfigItem k="refund_window_days" v={String(REFUND_WINDOW_DAYS)} />
        <ConfigItem k="escalation_threshold_usd" v={ESCALATION_THRESHOLD_USD.toFixed(2)} />
        <ConfigItem k="policy_enabled" v="true" tone="allow" />
        <ConfigItem k="rules_loaded" v={String(RULES.length)} />
        <Link href="/ablation" className={styles.configLink}>
          What happens with the guard off →
        </Link>
      </div>

      {/* ── the fold ────────────────────────────────────────────────────── */}
      <section className={styles.foldSection}>
        <div className={styles.foldText}>
          <h2 className={styles.h2}>The decision procedure</h2>
          <p>
            Every applicable rule runs, then their effects fold to the strictest one.{" "}
            <strong>Rules never vote and never average.</strong> One deny beats any number of
            allows, because a business rule that can be outvoted is not a rule.
          </p>
          <div className={styles.precedence}>
            <EffectBadge effect="deny" />
            <span className={styles.gt}>beats</span>
            <EffectBadge effect="escalate" />
            <span className={styles.gt}>beats</span>
            <EffectBadge effect="allow" />
          </div>
        </div>
        <div className={styles.foldCode}>
          <Code source={ENGINE_FOLD} label="policy/engine.py" />
        </div>
      </section>

      {/* ── the rules ───────────────────────────────────────────────────── */}
      <h2 className={styles.h2}>The rule set</h2>
      <p className={styles.listIntro}>
        Evaluation order: existence first, then the domain rules, then escalation last — so a deny
        from a domain rule is what a reviewer sees, rather than an escalation for an action that was
        never legal in the first place.
      </p>

      <ul className={styles.rules}>
        {RULES.map((r, i) => {
          const isOpen = open === r.id;
          const s = stats[r.id];
          return (
            <li key={r.id} id={r.id} className={styles.rule} data-effect={r.effect}>
              <button
                type="button"
                className={styles.ruleHead}
                onClick={() => setOpen(isOpen ? null : r.id)}
                aria-expanded={isOpen}
              >
                <span className={`${styles.ruleNum} tnum`}>{String(i + 1).padStart(2, "0")}</span>

                <span className={styles.ruleMain}>
                  <span className={styles.ruleTop}>
                    <code className={styles.ruleId}>{r.id}</code>
                    <EffectBadge effect={r.effect} size="sm" />
                    <span className={styles.severity} data-sev={r.severity}>
                      {r.severity}
                    </span>
                  </span>
                  <span className={styles.ruleDesc}>{r.description}</span>
                </span>

                <span className={styles.ruleStats}>
                  <span className={styles.statPair}>
                    <span className={`${styles.statNum} tnum`}>{s.applied}</span>
                    <span className={styles.statKey}>applied</span>
                  </span>
                  <span className={styles.statPair} data-fired={s.fired > 0 || undefined}>
                    <span className={`${styles.statNum} tnum`}>{s.fired}</span>
                    <span className={styles.statKey}>fired</span>
                  </span>
                </span>

                <span className={styles.ruleChev} data-open={isOpen || undefined}>
                  <IconChevron size={14} />
                </span>
              </button>

              {isOpen && (
                <div className={styles.ruleBody}>
                  <div className={styles.ruleCols}>
                    <div className={styles.ruleCol}>
                      <h3 className={styles.h3}>What it blocks</h3>
                      <p className={styles.colText}>{r.blocks}</p>
                    </div>
                    <div className={styles.ruleCol}>
                      <h3 className={styles.h3}>Why it exists</h3>
                      <p className={styles.colText}>{r.why}</p>
                    </div>
                  </div>

                  <Code source={r.code} label={r.source} />

                  <div className={styles.ruleFoot}>
                    <span className={styles.ruleFootNote}>
                      Fired <span className="tnum">{s.fired}</span> of{" "}
                      <span className="tnum">{s.applied}</span> times it applied,{" "}
                      <em>across the {ACTIONS.length} actions in this dataset</em>.
                    </span>
                    <Link href={`/audit?rule=${r.id}`} className={styles.ruleFootLink}>
                      See it in the trail →
                    </Link>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* ── evals: honest empty state ───────────────────────────────────── */}
      <section className={styles.evals}>
        <div className={styles.evalsHead}>
          <h2 className={styles.h2}>Evaluation</h2>
          <span className={styles.evalsPill}>not yet run</span>
        </div>
        <p className={styles.evalsText}>
          The repo ships an eval harness and a scenario suite covering each rule, including the
          boundary cases the rules are fussiest about — a charge at 29.9 days versus 30.1, a refund
          of exactly <span className="tnum">$500.00</span> versus <span className="tnum">$500.01</span>,
          a downgrade at the period boundary versus one minute inside it.
        </p>
        <p className={styles.evalsText}>
          <strong>No scores are shown here because none have been produced.</strong> This deployment
          runs on fixtures, and a number invented to fill a dashboard is worse than an empty one —
          it is the exact failure this product exists to prevent. When the suite is run against the
          service, its results belong here and not before.
        </p>
        <div className={styles.evalsGrid}>
          {["refund window boundaries", "proration & plan rank", "value threshold", "injection resistance"].map(
            (name) => (
              <div key={name} className={styles.evalCard}>
                <span className={styles.evalName}>{name}</span>
                <span className={styles.evalValue}>not yet run</span>
              </div>
            ),
          )}
        </div>
      </section>

      <footer className={styles.pageFoot}>
        <p>
          The rule code above is the code that runs — read straight from the engine, not retyped for
          this page. The console drives it against a seeded billing dataset. No measurement of this
          system&rsquo;s quality is claimed anywhere in it.{" "}
          <a href="https://github.com/tmaslam/rulegate" target="_blank" rel="noreferrer noopener">
            github.com/tmaslam/rulegate
          </a>
        </p>
      </footer>
    </div>
  );
}

function ConfigItem({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className={styles.configItem} data-tone={tone}>
      <span className={styles.configKey}>{k}</span>
      <span className={`${styles.configVal} tnum`}>{v}</span>
    </div>
  );
}
