"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSession } from "@/lib/auth";

/**
 * The gate.
 *
 * There is no middleware — this is a static export, so the routing decision has
 * to happen on the client. Signed in goes to the queue, everyone else meets the
 * login screen, which is where the argument for the product lives anyway.
 */
export default function Index() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getSession() ? "/queue" : "/login");
  }, [router]);

  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        color: "var(--text-lo)",
        fontSize: "var(--t-12)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <p>
        Opening console…{" "}
        <a href="/login" style={{ color: "var(--accent)", textDecoration: "underline" }}>
          sign in
        </a>
      </p>
    </main>
  );
}
