import { EVAL_NOW } from "./fixtures";

/** Money, always two places, always tabular. Never a float in the service —
 *  the fixtures carry numbers only because JSON has no Decimal. */
export function usd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Compact money for dense rows: $4,000 not $4,000.00 when the cents are zero. */
export function usdShort(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const whole = Number.isInteger(n);
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  });
}

const EVAL_MS = new Date(EVAL_NOW).getTime();

/** Relative time against the fixture's evaluation instant, so the demo reads
 *  the same in a year as it does today. */
export function ago(iso: string): string {
  const diff = EVAL_MS - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

/** 14 Jul, 09:12:41 UTC — the format an operator scanning a log wants. */
export function stamp(iso: string): string {
  const d = new Date(iso);
  const day = d.getUTCDate().toString().padStart(2, "0");
  const mon = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  const t = d.toISOString().slice(11, 19);
  return `${day} ${mon} ${t}`;
}

export function timeOnly(iso: string): string {
  return new Date(iso).toISOString().slice(11, 19);
}

export function dateOnly(iso: string): string {
  const d = new Date(iso);
  const day = d.getUTCDate().toString().padStart(2, "0");
  const mon = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  return `${day} ${mon} ${d.getUTCFullYear()}`;
}

/** Render a tool-call argument map the way the service logs it. */
export function argLine(args: Record<string, string | number | boolean>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : String(v)}`)
    .join(", ");
}
