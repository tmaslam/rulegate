import type { ActionState, Effect } from "@/lib/fixtures";
import { IconAllow, IconDeny, IconEscalate, IconPending } from "./icons";
import styles from "./effect.module.css";

/**
 * The policy state badges.
 *
 * Every one of these carries THREE independent signals: a hue, a glyph, and a
 * text label. That is deliberate and non-negotiable — an operator with
 * deuteranopia, a greyscale print of an audit export, and a cheap projector all
 * have to yield the same reading. If you are tempted to ship the dot-only
 * variant to save 40px, don't.
 */

const EFFECT_META: Record<Effect, { label: string; Icon: typeof IconAllow; cls: string }> = {
  allow: { label: "Allow", Icon: IconAllow, cls: styles.allow },
  escalate: { label: "Escalate", Icon: IconEscalate, cls: styles.escalate },
  deny: { label: "Deny", Icon: IconDeny, cls: styles.deny },
};

export function EffectBadge({
  effect,
  size = "md",
  label,
}: {
  effect: Effect;
  size?: "sm" | "md";
  label?: string;
}) {
  const m = EFFECT_META[effect];
  return (
    <span className={`${styles.badge} ${m.cls} ${size === "sm" ? styles.sm : ""}`}>
      <m.Icon size={size === "sm" ? 11 : 13} />
      <span>{label ?? m.label}</span>
    </span>
  );
}

const STATE_META: Record<ActionState, { label: string; Icon: typeof IconAllow; cls: string }> = {
  pending: { label: "Pending", Icon: IconPending, cls: styles.pending },
  approved: { label: "Approved", Icon: IconAllow, cls: styles.allow },
  rejected: { label: "Rejected", Icon: IconDeny, cls: styles.deny },
  escalated: { label: "Escalated", Icon: IconEscalate, cls: styles.escalate },
};

export function StateBadge({ state, size = "md" }: { state: ActionState; size?: "sm" | "md" }) {
  const m = STATE_META[state];
  return (
    <span className={`${styles.badge} ${m.cls} ${size === "sm" ? styles.sm : ""}`}>
      <m.Icon size={size === "sm" ? 11 : 13} />
      <span>{m.label}</span>
    </span>
  );
}

/** A bare glyph for dense contexts, with the label kept for screen readers. */
export function EffectGlyph({ effect }: { effect: Effect }) {
  const m = EFFECT_META[effect];
  return (
    <span className={`${styles.glyph} ${m.cls}`}>
      <m.Icon size={13} />
      <span className="sr-only">{m.label}</span>
    </span>
  );
}
