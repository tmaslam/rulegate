import type { Metadata } from "next";
import { Suspense } from "react";
import { QueueView } from "@/components/queue/QueueView";

export const metadata: Metadata = {
  title: "Approval queue",
  description: "Every action the agent has proposed, and what the policy engine decided about it.",
};

export default function QueuePage() {
  return (
    <Suspense fallback={null}>
      <QueueView />
    </Suspense>
  );
}
