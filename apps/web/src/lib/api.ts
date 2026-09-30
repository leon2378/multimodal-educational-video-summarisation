import createClient from "openapi-fetch";

import type { components, paths } from "./api/schema";
import { authHeaders } from "./auth";

/** The API's address as the browser sees it. Baked in at build time (NEXT_PUBLIC_*). */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Typed client generated from the API's OpenAPI schema (pnpm gen:api). Every call carries the
 *  session token when there is one. */
export const api = createClient<paths>({ baseUrl: API_URL });
api.use({
  async onRequest({ request }) {
    for (const [name, value] of Object.entries(await authHeaders())) request.headers.set(name, value);
    return request;
  },
});

type Schemas = components["schemas"];
export type Lecture = Schemas["LectureOut"];
export type LectureStatus = Schemas["LectureStatus"];
export type TranscriptLine = Schemas["TranscriptLineOut"];
export type Slide = Schemas["SlideOut"];
export type NotesResponse = Schemas["NotesOut"];
export type StudyNotes = Schemas["StudyNotes"];
export type ProgressEvent = Schemas["ProgressEvent"];
export type SearchHit = Schemas["SearchHitOut"];
export type AskEvent = Schemas["AskEvent"];
export type ChatMessage = Schemas["MessageOut"];
export type Source = Schemas["SourceOut"];
export type Citation = Schemas["CitationOut"];
export type Rating = Schemas["Rating"];
export type Course = Schemas["CourseOut"];
export type CourseDetail = Schemas["CourseDetail"];
export type Me = Schemas["MeOut"];
export type Quotas = Schemas["QuotaUsage"];
export type Visibility = Schemas["Visibility"];

/** A refused request: its status (401 sign in, 403 not yours, 404 not visible to you, 413 too
 *  large, 429 a limit) and the API's message, with how long a limit asks to wait. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterS: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Retry-After in seconds, from either form the header takes. */
export function retryAfterSeconds(response: Response, now: number = Date.now()): number | null {
  const value = response.headers.get("Retry-After");
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, Math.round((at - now) / 1000));
}

export function errorFrom(response: Response, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  return new ApiError(
    typeof detail === "string" ? detail : `HTTP ${response.status}`,
    response.status,
    retryAfterSeconds(response),
  );
}

/** The response body, or an ApiError carrying the API's message, so TanStack Query shows it. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data === undefined) throw errorFrom(result.response, result.error);
  return result.data;
}

/** For calls that answer without a body (204): throws like unwrap when refused. */
export function ensureOk(result: { error?: unknown; response: Response }): void {
  if (!result.response.ok) throw errorFrom(result.response, result.error);
}
