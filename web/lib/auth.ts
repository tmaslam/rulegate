/**
 * Demo auth.
 *
 * This is a static export with no server, so there is nothing to authenticate
 * against and nothing to protect — the "session" is a flag in localStorage and
 * any credentials are accepted. It exists to make the console feel like the
 * product it is modelled on, not to secure anything. Do not mistake this for an
 * auth implementation; the real service puts the console behind its own SSO.
 */

const KEY = "rulegate.session";

export type Session = {
  email: string;
  name: string;
  role: string;
  since: number;
};

export const DEMO_EMAIL = "demo@rulegate.app";
export const DEMO_PASSWORD = "demo1234";

/** Turn an email into a plausible operator identity for the header. */
function identify(email: string): Session {
  const handle = (email.split("@")[0] || "demo").replace(/[._-]+/g, " ").trim();
  const name = handle
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
  return {
    email,
    name: name || "Demo Operator",
    role: "Billing ops · approver",
    since: Date.now(),
  };
}

export function signIn(email: string): Session {
  const session = identify(email.trim() || DEMO_EMAIL);
  try {
    localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* private mode — the session simply does not persist across reloads */
  }
  return session;
}

export function signOut(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    return parsed && typeof parsed.email === "string" ? parsed : null;
  } catch {
    return null;
  }
}
