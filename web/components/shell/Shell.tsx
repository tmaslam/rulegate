"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getSession, signOut, type Session } from "@/lib/auth";
import { countByState } from "@/lib/fixtures";
import {
  IconAblation,
  IconAudit,
  IconCommand,
  IconQueue,
  IconRules,
  IconSearch,
  IconSignOut,
  Mark,
} from "../icons";
import { ThemeToggle } from "../ThemeToggle";
import { CommandPalette } from "./CommandPalette";
import styles from "./shell.module.css";

const NAV = [
  { href: "/queue", label: "Queue", Icon: IconQueue, key: "1" },
  { href: "/audit", label: "Audit", Icon: IconAudit, key: "2" },
  { href: "/rules", label: "Rules", Icon: IconRules, key: "3" },
  { href: "/ablation", label: "Guard on/off", Icon: IconAblation, key: "4" },
];

/**
 * The console shell: a 52px icon rail, a command bar, and the view.
 *
 * The rail is icon-only and always visible rather than a collapsible sidebar —
 * on a screen someone lives in all day, navigation that moves is navigation you
 * re-learn every morning. Labels ride in a tooltip and in the accessible name.
 *
 * This also holds the auth gate. No middleware exists (static export), so the
 * check is a client effect: unauthenticated visitors land back on /login.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const s = getSession();
    setSession(s);
    if (!s) router.replace("/login");
  }, [router]);

  // Keyboard-first: "/" focuses search, ⌘K / Ctrl-K opens the palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing =
        el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const out = () => {
    signOut();
    router.replace("/login");
  };

  // Hold the frame until we know. Rendering the console to a signed-out visitor
  // for one flash and then yanking it is worse than a beat of nothing.
  if (session === undefined) {
    return <div className={styles.booting} aria-hidden />;
  }
  if (session === null) return null;

  const pending = countByState().pending;
  const initials = session.name
    .split(" ")
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

  return (
    <>
      <a href="#view" className="skip">
        Skip to content
      </a>

      <div className={styles.shell}>
        {/* ── rail ───────────────────────────────────────────────────── */}
        <nav className={styles.rail} aria-label="Console sections">
          <Link href="/queue" className={styles.railMark} aria-label="PolicyGuard home">
            <Mark size={22} />
          </Link>

          <ul className={styles.railNav}>
            {NAV.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={styles.railLink}
                    data-active={active || undefined}
                    aria-current={active ? "page" : undefined}
                  >
                    <item.Icon size={16} />
                    <span className={styles.railTip} role="tooltip">
                      {item.label}
                    </span>
                    <span className="sr-only">{item.label}</span>
                    {item.href === "/queue" && pending > 0 && (
                      <span className={styles.railBadge} aria-label={`${pending} pending`}>
                        {pending}
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>

          <div className={styles.railFoot}>
            <ThemeToggle compact />
          </div>
        </nav>

        {/* ── main column ────────────────────────────────────────────── */}
        <div className={styles.column}>
          <header className={styles.bar}>
            <button
              type="button"
              className={styles.search}
              onClick={() => setPaletteOpen(true)}
              ref={searchRef as unknown as React.RefObject<HTMLButtonElement>}
            >
              <IconSearch size={13} />
              <span className={styles.searchText}>Search actions, customers, rules</span>
              <kbd className={styles.kbd}>/</kbd>
            </button>

            <div className={styles.barRight}>
              <button
                type="button"
                className={styles.cmdBtn}
                onClick={() => setPaletteOpen(true)}
                aria-label="Open command palette"
              >
                <IconCommand size={11} />
                <span>K</span>
              </button>

              <span className={styles.divider} aria-hidden />

              <div className={styles.user}>
                <span className={styles.avatar} aria-hidden>
                  {initials}
                </span>
                <span className={styles.userText}>
                  <strong>{session.name}</strong>
                  <span>{session.role}</span>
                </span>
              </div>

              <button type="button" className={styles.iconBtn} onClick={out} aria-label="Sign out">
                <IconSignOut size={14} />
              </button>
            </div>
          </header>

          <main className={styles.view} id="view">
            {children}
          </main>
        </div>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
