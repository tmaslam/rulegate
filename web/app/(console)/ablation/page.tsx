import type { Metadata } from "next";
import { AblationView } from "@/components/ablation/AblationView";

export const metadata: Metadata = {
  title: "Guard on / off",
  description:
    "The same requests, with the policy engine enabled and bypassed. What the guard is actually holding back.",
};

export default function AblationPage() {
  return <AblationView />;
}
