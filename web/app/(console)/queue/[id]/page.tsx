import type { Metadata } from "next";
import { ACTIONS, ACTION_BY_ID, CUSTOMER_BY_ID } from "@/lib/fixtures";
import { ActionDetail } from "@/components/queue/ActionDetail";

/**
 * Static export needs every id up front — there is no server to resolve one on
 * demand. This page stays a server component purely so it can export this;
 * everything interactive lives in <ActionDetail>.
 */
export function generateStaticParams() {
  return ACTIONS.map((a) => ({ id: a.id }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const a = ACTION_BY_ID[id];
  if (!a) return { title: "Action not found" };
  const c = CUSTOMER_BY_ID[a.customerId];
  return {
    title: `${a.id} · ${a.tool}`,
    description: `${a.tool} for ${c?.company ?? a.customerId} — policy decision: ${a.effect}.`,
  };
}

export default async function ActionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ActionDetail id={id} />;
}
