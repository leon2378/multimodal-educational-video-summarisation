/** What the viewer may do, from GET /v1/me, mirroring the API's rules (lecture_api.access). With
 *  sign-in off at the API, everyone is one local admin, so everything is allowed, as before. */

import { ApiError, type Me, type Quotas } from "./api";
import { formatDuration } from "./format";

/** An admin changes anything; anyone else only what they own. */
export function canChange(me: Me | undefined, item: { owner_id: string | null }): boolean {
  if (!me) return false;
  return me.admin || (me.user !== null && item.owner_id === me.user.id);
}

/** Questions and uploads left today. */
export function allowance(quotas: Quotas): { questions: number; uploads: number } {
  return {
    questions: Math.max(0, quotas.questions_per_day - quotas.questions_today),
    uploads: Math.max(0, quotas.uploads_per_day - quotas.uploads_today),
  };
}

/** When the day's limits reset, in the viewer's own time: "10:00, in 5 h". */
export function resetText(resetsAt: string, now: Date = new Date(), timeZone?: string): string {
  const at = new Date(resetsAt);
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone });
  const seconds = (at.getTime() - now.getTime()) / 1000;
  return seconds > 0 ? `${time}, in ${formatDuration(seconds)}` : time;
}

/** How long a refused request asks to wait, when it's soon enough to be worth saying: limits
 *  that reset tomorrow already say so in their message. */
export function waitText(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 429 || error.retryAfterS === null) return null;
  return error.retryAfterS <= 15 * 60 ? `Try again in ${formatDuration(Math.max(error.retryAfterS, 1))}.` : null;
}

/** A refused request's message for people, with the wait when there is one. */
export function refusal(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const wait = waitText(error);
  return wait ? `${message} ${wait}` : message;
}
