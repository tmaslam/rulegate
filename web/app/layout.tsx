import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "RuleGate — ops console",
    template: "%s · RuleGate",
  },
  description:
    "The approval console for an agent that issues refunds, changes plans and cancels subscriptions. Every action it proposes is approved, rejected or escalated by a deterministic policy engine — real code, not a prompt.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0a08" },
    { media: "(prefers-color-scheme: light)", color: "#f2f1ea" },
  ],
};

/**
 * Applied before first paint so a stored theme choice never flashes the wrong
 * one. Kept tiny and dependency-free on purpose — it runs on every page load
 * ahead of the bundle, and an ops console that strobes white at 3am is a bug
 * worth this much inline script.
 */
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('policyguard.theme');if(t==='light'||t==='dark'){document.documentElement.dataset.theme=t;}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
