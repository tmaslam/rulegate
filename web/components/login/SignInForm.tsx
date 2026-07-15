"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { DEMO_EMAIL, DEMO_PASSWORD, signIn } from "@/lib/auth";
import { IconArrowRight, IconBolt, IconLock, IconUser } from "../icons";
import { ThemeToggle } from "../ThemeToggle";
import styles from "./form.module.css";

/**
 * The sign-in form.
 *
 * A stage set, and honest about it: there is no server here, so any credentials
 * are accepted and the demo pair is printed on the panel. That is the whole
 * design intent — a portfolio visitor who meets a login wall with no way
 * through just closes the tab. Two ways in, both instant, neither hidden.
 *
 * This component owns state, which is exactly why it is NOT part of the
 * animated hero subtree next door — a re-render there would re-fire the GSAP
 * intro and strand it.
 */
export function SignInForm() {
  const router = useRouter();
  const [email, setEmail] = useState(DEMO_EMAIL);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [busy, setBusy] = useState<"form" | "demo" | null>(null);
  const timer = useRef<number | null>(null);

  const go = (who: "form" | "demo") => {
    if (busy) return;
    setBusy(who);
    signIn(who === "demo" ? DEMO_EMAIL : email);
    // One frame of acknowledgement, then in. Long enough to read as a response
    // to the click, short enough that nobody calls it a loading screen.
    timer.current = window.setTimeout(() => router.push("/queue"), 240);
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    go("form");
  };

  return (
    <div className={styles.panel}>
      <div className={styles.panelTop}>
        <span className={styles.env}>
          <span className={styles.envDot} aria-hidden />
          production · eu-west
        </span>
        <ThemeToggle compact />
      </div>

      <div className={styles.panelBody}>
        <header className={styles.head}>
          <h1 className={styles.title}>Sign in</h1>
          <p className={styles.sub}>Billing operations console. Approver access.</p>
        </header>

        <form onSubmit={onSubmit} className={styles.form}>
          <div className={styles.field}>
            <label htmlFor="email" className={styles.label}>
              Work email
            </label>
            <div className={styles.inputWrap}>
              <span className={styles.inputIcon} aria-hidden>
                <IconUser size={14} />
              </span>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                className={`${styles.input} mono`}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
          </div>

          <div className={styles.field}>
            <label htmlFor="password" className={styles.label}>
              Password
            </label>
            <div className={styles.inputWrap}>
              <span className={styles.inputIcon} aria-hidden>
                <IconLock size={14} />
              </span>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                className={`${styles.input} mono`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </div>

          <button type="submit" className={styles.primary} disabled={busy !== null}>
            <span>{busy === "form" ? "Signing in…" : "Sign in"}</span>
            <IconArrowRight size={14} />
          </button>
        </form>

        <div className={styles.divider}>
          <span>or</span>
        </div>

        <button type="button" className={styles.secondary} onClick={() => go("demo")} disabled={busy !== null}>
          <IconBolt size={14} />
          <span>{busy === "demo" ? "Opening console…" : "Continue as demo user"}</span>
        </button>

        <aside className={styles.demoNote} aria-label="Demo access credentials">
          <span className={styles.demoKey}>Demo access</span>
          <p>
            <code>{DEMO_EMAIL}</code> / <code>{DEMO_PASSWORD}</code> — already filled in. Or use the
            button above. Any credentials are accepted.
          </p>
        </aside>
      </div>

      <footer className={styles.panelFoot}>
        <p>
          Demo build. The console runs on fixture data — no live billing system is attached.{" "}
          <a
            href="https://github.com/tmaslam/policy-guard"
            target="_blank"
            rel="noreferrer noopener"
            className={styles.link}
          >
            Source on GitHub
          </a>
        </p>
      </footer>
    </div>
  );
}
