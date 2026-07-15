import { Shell } from "@/components/shell/Shell";

/**
 * Every in-app route shares the shell (rail + command bar + auth gate).
 * A route group rather than a path segment, so the URLs stay /queue, /audit,
 * /rules — an operator pasting a link into a ticket should not be pasting
 * /console/queue.
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <Shell>{children}</Shell>;
}
