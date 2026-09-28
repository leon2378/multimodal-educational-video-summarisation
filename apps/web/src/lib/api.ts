import createClient from "openapi-fetch";

import type { components, paths } from "./api/schema";

/** The API's address as the browser sees it. Baked in at build time (NEXT_PUBLIC_*). */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Typed client generated from the API's OpenAPI schema (pnpm gen:api). */
export const api = createClient<paths>({ baseUrl: API_URL });

type Schemas = components["schemas"];
export type Lecture = Schemas["LectureOut"];
export type LectureStatus = Schemas["LectureStatus"];
export type TranscriptLine = Schemas["TranscriptLineOut"];
export type Slide = Schemas["SlideOut"];
export type NotesResponse = Schemas["NotesOut"];
export type StudyNotes = Schemas["StudyNotes"];
export type ProgressEvent = Schemas["ProgressEvent"];

/** The response body, or an Error carrying the API's message, so TanStack Query shows it. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data === undefined) {
    const detail = (result.error as { detail?: unknown } | undefined)?.detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${result.response.status}`);
  }
  return result.data;
}
