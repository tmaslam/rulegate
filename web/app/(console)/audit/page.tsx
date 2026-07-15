import type { Metadata } from "next";
import { AuditView } from "@/components/audit/AuditView";

export const metadata: Metadata = {
  title: "Audit trail",
  description:
    "Every decision, tool call, policy check and human approval, in order, with the evidence behind each one.",
};

export default function AuditPage() {
  return <AuditView />;
}
