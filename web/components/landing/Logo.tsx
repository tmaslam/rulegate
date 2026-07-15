/**
 * The RuleGate mark.
 *
 * A request (the dot) meets the gate (the bar) and stops. That is the entire
 * product, and it is the whole reason the mark is only two shapes — it has to
 * survive being rendered at 16px in a browser tab.
 *
 * Colours come from currentColor and the accent token rather than being baked
 * in, so the mark follows the theme. The static icon.svg/apple-icon.svg files
 * carry hard-coded values instead, since a favicon has no CSS to inherit.
 */
export default function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="RuleGate"
      style={{ display: "block", flex: "none" }}
    >
      <rect width="32" height="32" rx="7.5" fill="var(--accent)" />
      <circle cx="10.5" cy="16" r="3.25" fill="var(--accent-fg)" />
      <rect x="18" y="6.5" width="4.5" height="19" rx="2.25" fill="var(--accent-fg)" />
    </svg>
  );
}
