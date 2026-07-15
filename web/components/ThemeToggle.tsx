"use client";

import { useEffect, useState } from "react";
import { IconMoon, IconSun } from "./icons";
import styles from "./theme.module.css";

/**
 * Theme toggle.
 *
 * Dark is this console's primary design, but the choice is the operator's and
 * it has to stick. Order of authority: explicit stored choice > OS preference.
 * The inline script in layout.tsx applies the stored choice before first paint;
 * this component only has to keep the button in sync with it.
 */

type Theme = "dark" | "light";

const KEY = "rulegate.theme";

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  // Start undefined so SSG markup and first client paint agree; the real value
  // arrives in the effect. The inline script has already painted the right
  // colours by then, so there is no flash to chase here.
  const [theme, setTheme] = useState<Theme | undefined>(undefined);

  useEffect(() => {
    const stored = (() => {
      try {
        return localStorage.getItem(KEY) as Theme | null;
      } catch {
        return null;
      }
    })();
    setTheme(stored ?? systemTheme());
  }, []);

  const toggle = () => {
    const next: Theme = (theme ?? systemTheme()) === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* choice just won't survive the reload */
    }
  };

  const isDark = theme === "dark";
  const label = `Switch to ${isDark ? "light" : "dark"} theme`;

  return (
    <button
      type="button"
      onClick={toggle}
      className={`${styles.toggle} ${compact ? styles.compact : ""}`}
      aria-label={label}
      title={label}
    >
      <span className={styles.icons} data-theme-state={theme ?? "light"}>
        <IconSun size={compact ? 14 : 15} />
        <IconMoon size={compact ? 14 : 15} />
      </span>
    </button>
  );
}
