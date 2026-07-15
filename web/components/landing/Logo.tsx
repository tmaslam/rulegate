/**
 * The RuleGate mark.
 *
 * Both halves of the name, in one glyph. A horizontal line is called a *rule*,
 * so the three bars are the rules; the split vertical bar is the *gate*. One
 * rule clears the gap and gets through, two stop dead at it — which is also
 * exactly what the product does.
 *
 * Four shapes, all axis-aligned, nothing thinner than ~3/32 of the box, so it
 * still resolves when rasterised small. `app/icon.svg` carries the same geometry
 * with hard-coded colours (a favicon has no CSS to inherit) — change both
 * together.
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
      <rect width="32" height="32" rx="7.5" fill="var(--accent-solid)" />

      {/* denied — stops short of the gate */}
      <rect x="6" y="8" width="10" height="3" rx="1.5" fill="var(--accent-fg)" opacity="0.5" />

      {/* allowed — the only one through the gap */}
      <rect x="6" y="14.5" width="20" height="3" rx="1.5" fill="var(--accent-fg)" />

      {/* denied */}
      <rect x="6" y="21" width="10" height="3" rx="1.5" fill="var(--accent-fg)" opacity="0.5" />

      {/* the gate — split, so the allowed rule passes through rather than over */}
      <rect x="18.5" y="5" width="3.5" height="8" rx="1.75" fill="var(--accent-fg)" />
      <rect x="18.5" y="19" width="3.5" height="8" rx="1.75" fill="var(--accent-fg)" />
    </svg>
  );
}
