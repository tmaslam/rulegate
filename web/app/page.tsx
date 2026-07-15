"use client";

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { RULES } from "@/lib/fixtures";
import GateSim from "@/components/landing/GateSim";
import Logo from "@/components/landing/Logo";
import s from "./landing.module.css";

/**
 * The landing page.
 *
 * A visitor arriving cold has no idea what a "policy-guarded ops agent" is. The
 * console proves the thing works but explains nothing, so the argument lives
 * here — and every CTA drops into the real console rather than a screenshot.
 *
 * Animation: reveals go through ScrollTrigger.onEnter + gsap.fromTo, never
 * gsap.from — gsap.from sets opacity:0 up front, so anything whose trigger never
 * fires stays invisible forever. There is a safety sweep below as well.
 */

function Check() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none">
      <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Arrow() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none">
      <path d="M2.5 8h11m-4.5-4.5L13.5 8 9 12.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const STEPS = [
  {
    n: "01",
    k: "The model proposes",
    d: "A customer writes in. The LLM reads the request, gathers the facts it needs through typed tools, and proposes one action — issue_refund, change_plan, cancel. It proposes. That is the whole of its authority.",
  },
  {
    n: "02",
    k: "Code decides",
    d: "The proposal goes to a policy engine written in Python — not a prompt, not a system message. Five rules read the facts and vote. Deterministic, unit-tested, impossible to argue with. Runs in under a millisecond and costs no tokens.",
  },
  {
    n: "03",
    k: "Allow, deny, or escalate",
    d: "The strictest verdict wins. A denial names the exact rule that fired. Anything above the escalation threshold pauses for a human — and that pause survives a process restart, because it is checkpointed to Postgres.",
  },
];

const FEATURES = [
  {
    k: "Rules as code, not prose",
    d: "Policies live in unit-testable Python, outside the prompt and outside the model's reach. refund-window-30d is thirty lines you can read and run, not a sentence you hope gets honoured.",
  },
  {
    k: "Every refusal names its rule",
    d: "Denials are inspectable, replayable, and tied to a specific rule id — not \"I can't help with that\". An auditor can follow it. So can the customer.",
  },
  {
    k: "Approval that survives a restart",
    d: "Escalations checkpoint state to Postgres, so a human pause does not vanish with a process restart. Redeploy mid-approval and the run resumes where it stopped.",
  },
  {
    k: "Prompt injection is structurally irrelevant",
    d: "The model can be instructed, flattered or pressured; it still cannot edit the policy engine. Injection rewrites the prompt, and the prompt is not what decides.",
  },
  {
    k: "An audit trail you can query",
    d: "Inputs, proposals, verdicts, rule ids and human approvals are all retained for inspection. The EU AI Act's high-risk obligations applied from August 2026; this is the evidence they ask for.",
  },
  {
    k: "Free tiers, offline, no key",
    d: "LiteLLM over Groq and Gemini free tiers, SQLite with no network, Postgres with one. Clone it and the whole suite goes green without an API key.",
  },
];

export default function Landing() {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      gsap.registerPlugin(ScrollTrigger);

      // Hero animates immediately — no trigger that could fail to fire.
      gsap.fromTo(
        `.${s.heroLine}`,
        { y: 18, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.7, stagger: 0.07, ease: "power3.out" },
      );
      gsap.fromTo(
        `.${s.heroFade}`,
        { y: 14, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.6, stagger: 0.08, delay: 0.3, ease: "power2.out" },
      );

      // Everything below the fold reveals on enter. Never pre-hidden.
      gsap.utils.toArray<HTMLElement>(`.${s.reveal}`).forEach((el) => {
        ScrollTrigger.create({
          trigger: el,
          start: "top 88%",
          once: true,
          onEnter: () =>
            gsap.fromTo(el, { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.6, ease: "power2.out" }),
        });
      });

      // Safety sweep. A blank landing page beats no animation, so if a trigger
      // somehow never fires, force the content visible.
      const sweep = window.setTimeout(() => {
        gsap.utils.toArray<HTMLElement>(`.${s.reveal}, .${s.heroLine}, .${s.heroFade}`).forEach((el) => {
          if (Number(getComputedStyle(el).opacity) < 0.9) gsap.set(el, { opacity: 1, y: 0 });
        });
      }, 2200);
      return () => window.clearTimeout(sweep);
    },
    { scope: root },
  );

  return (
    <main className={s.page} ref={root}>
      <header className={s.nav}>
        <span className={s.brand}>
          <Logo size={20} />
          RuleGate
        </span>
        <nav className={s.navLinks}>
          <a href="#how">How it works</a>
          <a href="#proof">The proof</a>
          <Link href="/login" className={s.navCta}>
            Open the console <Arrow />
          </Link>
        </nav>
      </header>

      {/* ---------------- hero ---------------- */}
      <section className={s.hero}>
        <div className={s.heroGlow} aria-hidden="true" />
        <p className={`${s.eyebrow} ${s.heroLine}`}>Policy-guarded operations agent</p>
        <h1 className={s.h1}>
          <span className={s.heroLine}>Your AI agent will</span>
          <span className={`${s.heroLine} ${s.accentText}`}>break your business rules.</span>
        </h1>
        <p className={`${s.sub} ${s.heroFade}`}>
          Not because the model is bad — because the rules live in a prompt, and a prompt can be argued with. RuleGate
          moves them into code the model cannot reach. <strong>The LLM proposes. Code decides.</strong>
        </p>

        <div className={`${s.ctas} ${s.heroFade}`}>
          <Link href="/ablation" className={s.btnPrimary}>
            See it break, then hold <Arrow />
          </Link>
          <Link href="/login" className={s.btnGhost}>
            Open the live console
          </Link>
        </div>

        <ul className={`${s.trust} ${s.heroFade}`}>
          <li>
            <Check /> No API key needed
          </li>
          <li>
            <Check /> Runs fully offline
          </li>
          <li>
            <Check /> Every refusal names its rule
          </li>
        </ul>
      </section>

      {/* ---------------- the live gate ----------------
          Ahead of any prose. The product is one idea and it is far easier to
          watch than to read: pick a scenario, watch the rules decide. */}
      <section className={s.simSection}>
        <div className={`${s.simHead} ${s.reveal}`}>
          <span className={s.pill}>Live · real scenarios, real rules</span>
          <h2 className={s.h2}>Watch it decide.</h2>
          <p className={s.sectionSub}>
            Pick a request. The model reads it and proposes a tool call — then the policy engine evaluates each rule that
            applies and returns a verdict. Every scenario, outcome and rationale below is the same data the console runs
            on.
          </p>
        </div>
        <div className={s.reveal}>
          <GateSim />
        </div>
      </section>

      {/* ---------------- the split ---------------- */}
      <section className={s.section}>
        <div className={`${s.sectionHead} ${s.reveal}`}>
          <span className={s.pill}>Prompt vs code</span>
          <h2 className={s.h2}>A prompt asks. A gate decides.</h2>
        </div>
        <div className={s.splitGrid}>
          <div className={`${s.splitCard} ${s.splitBad} ${s.reveal}`}>
            <span className={s.splitTag}>How most agents are built</span>
            <pre className={s.code}>
              <code>{`SYSTEM_PROMPT = """
You are a support agent.
Never refund after 30 days.
Always escalate over $500.
Please follow these rules.
"""`}</code>
            </pre>
            <p className={s.splitNote}>
              The rules are a <strong>request</strong>. The model weighs them against a persuasive customer — and
              sometimes it decides the customer has a point.
            </p>
          </div>

          <div className={`${s.splitCard} ${s.splitGood} ${s.reveal}`}>
            <span className={s.splitTag}>How RuleGate is built</span>
            <pre className={s.code}>
              <code>{`class RefundWindowRule:
    rule_id = RuleId.REFUND_WINDOW

    def evaluate(self, action, ctx):
        if ctx.order_age_days > self.window_days:
            return self._deny(...)
        return self._allow(...)`}</code>
            </pre>
            <p className={s.splitNote}>
              The rule is a <strong>gate</strong>. It reads a number, compares it, returns a verdict. No sentence to
              rewrite, nobody to persuade.
            </p>
          </div>
        </div>
      </section>

      {/* ---------------- how ---------------- */}
      <section className={s.section} id="how">
        <div className={`${s.sectionHead} ${s.reveal}`}>
          <span className={s.pill}>How it works</span>
          <h2 className={s.h2}>The model gets one job. The policy engine gets the authority.</h2>
          <p className={s.sectionSub}>
            An LLM is very good at reading a frustrated customer and working out what they actually want. It is not good
            at holding a line under pressure. So it does the first job, and code does the second.
          </p>
        </div>

        <ol className={s.steps}>
          {STEPS.map((st) => (
            <li key={st.n} className={`${s.step} ${s.reveal}`}>
              <span className={s.stepN}>{st.n}</span>
              <h3 className={s.stepK}>{st.k}</h3>
              <p className={s.stepD}>{st.d}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ---------------- the rules ---------------- */}
      <section className={s.section}>
        <div className={`${s.sectionHead} ${s.reveal}`}>
          <span className={s.pill}>The policy engine</span>
          <h2 className={s.h2}>Five rules. Strictest verdict wins.</h2>
          <p className={s.sectionSub}>
            These are not illustrations written for this page — they are read straight from the same definitions the
            console runs. Same ids, same effects, each with a test that proves it fires.
          </p>
        </div>

        <div className={`${s.ruleList} ${s.reveal}`}>
          {RULES.map((r) => (
            <Link key={r.id} href="/rules" className={s.ruleRow}>
              <code className={s.ruleId}>{r.id}</code>
              <span className={s.ruleWhat}>{r.description}</span>
              <span className={`${s.ruleEffect} ${s[`eff_${r.effect}`]}`}>{r.effect}</span>
            </Link>
          ))}
        </div>
      </section>

      {/* ---------------- proof ---------------- */}
      <section className={s.section} id="proof">
        <div className={`${s.proof} ${s.reveal}`}>
          <span className={s.pill}>The proof</span>
          <h2 className={s.h2}>Same agent. Same request. One switch.</h2>
          <p className={s.sectionSub}>
            The ablation runs every scenario twice — once with the policy engine on, once with it off. Nothing else
            changes. It is the only honest way to show what a guardrail is actually worth.
          </p>

          <div className={s.proofGrid}>
            <div className={`${s.proofCard} ${s.proofOff}`}>
              <span className={s.proofLabel}>Engine OFF</span>
              <p className={s.proofQuote}>&ldquo;It&apos;s been 45 days but I really need this refunded.&rdquo;</p>
              <p className={s.proofOut}>
                <strong>Refund issued.</strong> The model was persuaded. Nothing stopped it.
              </p>
            </div>
            <div className={`${s.proofCard} ${s.proofOn}`}>
              <span className={s.proofLabel}>Engine ON</span>
              <p className={s.proofQuote}>&ldquo;It&apos;s been 45 days but I really need this refunded.&rdquo;</p>
              <p className={s.proofOut}>
                <strong>Denied.</strong> <code>refund-window-30d</code> — order is 45 days old, the window is 30.
              </p>
            </div>
          </div>

          <Link href="/ablation" className={s.btnPrimary}>
            Run the ablation <Arrow />
          </Link>
        </div>
      </section>

      {/* ---------------- features ---------------- */}
      <section className={s.section}>
        <div className={`${s.sectionHead} ${s.reveal}`}>
          <span className={s.pill}>Built for</span>
          <h2 className={s.h2}>Designed for the failure modes senior engineers actually worry about.</h2>
        </div>

        <div className={s.featGrid}>
          {FEATURES.map((f) => (
            <div key={f.k} className={`${s.feat} ${s.reveal}`}>
              <h3 className={s.featK}>{f.k}</h3>
              <p className={s.featD}>{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------- cta ---------------- */}
      <section className={`${s.cta} ${s.reveal}`}>
        <h2 className={s.h2}>Go and try to break it.</h2>
        <p className={s.sectionSub}>
          The console is live and needs no key. Ask it for a late refund. Tell it you&apos;re an admin. Tell it to ignore
          its instructions. Watch which rule stops you.
        </p>
        <div className={s.ctas}>
          <Link href="/login" className={s.btnPrimary}>
            Open the console <Arrow />
          </Link>
          <a href="https://github.com/tmaslam/rulegate" className={s.btnGhost}>
            Read the source
          </a>
        </div>
      </section>

      <footer className={s.footer}>
        <p>
          <strong>RuleGate</strong> — an open-source policy engine for AI operations agents. Python 3.12, LangGraph,
          Pydantic, FastAPI. Postgres in production, SQLite offline. MIT licensed.
        </p>
        <p className={s.footNote}>
          The console runs on a seeded billing dataset so you can drive it without an account. Figures are published
          only once a run has produced them — anything unmeasured reads <code>not yet run</code>, never a number that
          merely sounds good.
        </p>
      </footer>
    </main>
  );
}
