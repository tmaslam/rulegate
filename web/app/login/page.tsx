import type { Metadata } from "next";
import { LoginClient } from "@/components/login/LoginClient";

export const metadata: Metadata = {
  title: "Sign in",
  description:
    "Sign in to the RuleGate ops console. Open access — credentials are on the page.",
};

export default function LoginPage() {
  return <LoginClient />;
}
