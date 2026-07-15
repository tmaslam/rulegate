import type { Metadata } from "next";
import { RulesView } from "@/components/rules/RulesView";

export const metadata: Metadata = {
  title: "Policy rules",
  description:
    "The rules themselves, as the code that runs. What each one blocks, and how often it fired in this dataset.",
};

export default function RulesPage() {
  return <RulesView />;
}
