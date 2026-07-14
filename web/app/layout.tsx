import type { Metadata } from "next";
import "./tokens.css";
import "./components.css";
import "./app.css";

export const metadata: Metadata = {
  title: "Policy-Guarded Ops Agent — live demo",
  description:
    "An AI agent that runs real customer-ops workflows and provably obeys business rules. The policy engine is deterministic code, not a prompt.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
