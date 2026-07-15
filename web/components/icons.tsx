/**
 * Inline SVG icons. Hand-drawn on a 16px grid, 1.5 stroke.
 *
 * No icon package: this console's identity has to be its own, and a kit would
 * drag it toward looking like every other dashboard. They are also load-bearing
 * for accessibility — every policy state pairs its colour with one of the
 * glyphs below, so the state survives greyscale, colour-blindness and a bad
 * projector. Colour is never the only signal.
 */

type P = { size?: number; className?: string };
const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false as const,
});

/* ── Policy states — each is a distinct SHAPE, not a tinted dot ─────────── */

/** allow — a check inside a circle. */
export const IconAllow = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M5.2 8.2 7 10l3.8-4" />
  </svg>
);

/** escalate — an upward chevron into a bar. "Goes to a person." */
export const IconEscalate = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M2.4 13.6h11.2" />
    <path d="M8 10.6V3.2" />
    <path d="M4.9 6.3 8 3.2l3.1 3.1" />
  </svg>
);

/** deny — a shield with a slash. Not an error triangle: the system worked. */
export const IconDeny = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M8 1.9 2.9 3.7v4.1c0 3 2.1 5.2 5.1 6.3 3-1.1 5.1-3.3 5.1-6.3V3.7z" />
    <path d="M5.9 9.9 10.1 5.6" />
  </svg>
);

/** pending — a clock. */
export const IconPending = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M8 4.5V8l2.4 1.6" />
  </svg>
);

/* ── Navigation ─────────────────────────────────────────────────────────── */

export const IconQueue = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M2.5 4.2h11M2.5 8h11M2.5 11.8h7" />
    <circle cx="12.4" cy="11.8" r="1.6" />
  </svg>
);

export const IconAudit = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M3.4 2.2h6.2l3 3v8.6H3.4z" />
    <path d="M9.4 2.3v3.1h3.1" />
    <path d="M5.6 8.4h4.8M5.6 10.9h3.2" />
  </svg>
);

export const IconRules = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="m5.6 5.4-3 2.6 3 2.6" />
    <path d="m10.4 5.4 3 2.6-3 2.6" />
    <path d="M9.2 2.9 6.8 13.1" />
  </svg>
);

export const IconAblation = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M8 1.8v12.4" strokeDasharray="1.8 1.6" />
    <path d="M4.9 4.6H2.4v6.8h2.5z" />
    <path d="M13.6 4.6h-2.5v6.8h2.5z" />
  </svg>
);

/* ── UI ─────────────────────────────────────────────────────────────────── */

export const IconSearch = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="7.1" cy="7.1" r="4.6" />
    <path d="m10.6 10.6 3 3" />
  </svg>
);

export const IconSun = ({ size = 15, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="8" cy="8" r="3.1" />
    <path d="M8 1.4v1.6M8 13v1.6M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M1.4 8H3M13 8h1.6M3.3 12.7l1.1-1.1M11.6 4.4l1.1-1.1" />
  </svg>
);

export const IconMoon = ({ size = 15, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M13.4 9.3A5.7 5.7 0 0 1 6.7 2.6a5.9 5.9 0 1 0 6.7 6.7" />
  </svg>
);

export const IconChevron = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="m6 3.5 4.5 4.5L6 12.5" />
  </svg>
);

export const IconArrowLeft = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M13 8H3" />
    <path d="m6.8 3.8 -3.8 4.2 3.8 4.2" />
  </svg>
);

export const IconArrowRight = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M3 8h10" />
    <path d="m9.2 3.8 3.8 4.2-3.8 4.2" />
  </svg>
);

export const IconFilter = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M2.2 3.4h11.6L9.3 8.6v4.3l-2.6-1.5V8.6z" />
  </svg>
);

export const IconTool = ({ size = 13, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M9.9 2.6a3.2 3.2 0 0 0 3.9 4.3l-8 8a1.7 1.7 0 0 1-2.4-2.4l8-8a3.2 3.2 0 0 0-1.5-1.9z" />
  </svg>
);

export const IconUser = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="8" cy="5.4" r="2.7" />
    <path d="M2.9 13.6a5.1 5.1 0 0 1 10.2 0" />
  </svg>
);

export const IconLock = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <rect x="3.2" y="7" width="9.6" height="6.8" rx="1.4" />
    <path d="M5.4 7V4.9a2.6 2.6 0 0 1 5.2 0V7" />
  </svg>
);

export const IconSignOut = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M6 13.6H3.4V2.4H6" />
    <path d="M9.6 11.2 12.8 8 9.6 4.8" />
    <path d="M12.8 8H6.2" />
  </svg>
);

export const IconBolt = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M8.9 1.6 3.4 9.1h4L7 14.4l5.6-7.5h-4z" />
  </svg>
);

export const IconInjection = ({ size = 14, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M8 1.9 1.6 13.2h12.8z" />
    <path d="M8 6.2v3.1M8 11.4h.01" />
  </svg>
);

export const IconGithub = ({ size = 14, className }: P) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden focusable={false} className={className}>
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
  </svg>
);

export const IconCommand = ({ size = 12, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M5.2 2.6a1.8 1.8 0 1 0 0 3.6h5.6a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0-1.8 1.8v7.6a1.8 1.8 0 1 0 3.6 0 1.8 1.8 0 0 0-1.8-1.8H5.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 1.8-1.8V4.4a1.8 1.8 0 0 0-1.8-1.8z" />
  </svg>
);

/** The RuleGate mark.
 *
 *  A horizontal line is a *rule*, so the three bars are the rules; the split
 *  vertical bar is the *gate*. One rule clears the gap and gets through, two
 *  stop dead at it — which is what the product does.
 *
 *  This was a shield, which said nothing the name did not already say, and it
 *  did not match the mark on the landing page. Same geometry as
 *  components/landing/Logo.tsx and app/icon.svg — change all three together.
 *  Drawn in currentColor with no badge, so the rail can tint it. */
export const Mark = ({ size = 22, className }: P) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden focusable={false} className={className}>
    {/* Geometry is identical to components/landing/Logo.tsx and app/icon.svg —
        it had drifted to its own coordinates, so the same product wore two
        slightly different marks depending on which page you were on. The only
        difference here is the badge: the rail tints the glyph itself, so this
        one draws in currentColor with no square behind it. */}
    {/* denied — stops short of the gate */}
    <rect x="6" y="8" width="10" height="3" rx="1.5" fill="currentColor" opacity="0.45" />
    {/* allowed — the only one through the gap */}
    <rect x="6" y="14.5" width="20" height="3" rx="1.5" fill="currentColor" />
    {/* denied */}
    <rect x="6" y="21" width="10" height="3" rx="1.5" fill="currentColor" opacity="0.45" />
    {/* the gate — split, so the allowed rule passes through rather than over */}
    <rect x="18.5" y="5" width="3.5" height="8" rx="1.75" fill="currentColor" />
    <rect x="18.5" y="19" width="3.5" height="8" rx="1.75" fill="currentColor" />
  </svg>
);
