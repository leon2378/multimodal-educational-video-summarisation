/** The session token for API calls, as code outside React (the typed client, the streams) sees
 *  it. With Clerk the bridge in components/account.tsx supplies the token once Clerk has loaded;
 *  without it, calls go out with no token, which the API takes as signed out or, with sign-in off
 *  at the API, as its one local user (docs/adr/0009-clerk-sign-in-and-quotas.md). */

/** Clerk is on when the web app was built with a publishable key (NEXT_PUBLIC_*, inlined). */
export const CLERK_KEY = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY || undefined;

type TokenSource = () => Promise<string | null>;

let source: TokenSource | null = null;
let markLoaded: () => void = () => {};
const loaded = new Promise<void>((resolve) => {
  markLoaded = resolve;
});
if (!CLERK_KEY) markLoaded();

/** Called once Clerk has loaded, with how to get a fresh session token (Clerk refreshes it). */
export function setTokenSource(next: TokenSource | null): void {
  source = next;
  markLoaded();
}

/** If Clerk doesn't load (blocked, offline), calls go out signed out after this rather than
 *  waiting for ever. */
const LOAD_TIMEOUT_MS = 8000;
let settled: Promise<unknown> | null = null;

/** The Authorization header for an API call, or none. Waits for Clerk to load, so the first
 *  requests of a visit don't go out signed out and cache what a visitor would see. Never sent
 *  to storage: presigned URLs carry their own signature. */
export async function authHeaders(): Promise<Record<string, string>> {
  settled ??= Promise.race([loaded, new Promise((resolve) => setTimeout(resolve, LOAD_TIMEOUT_MS))]);
  await settled;
  const token = source ? await source().catch(() => null) : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
