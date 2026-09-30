"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_URL, ApiError, type ProgressEvent, api, unwrap } from "./api";
import type { Scope } from "./scope";
import { streamEvents } from "./stream";

/** Every query for a lecture starts with ["lecture", id], so one invalidation refreshes it all. */
export const lectureKey = (id: string, ...rest: string[]) => ["lecture", id, ...rest];
const key = lectureKey;
/** Likewise ["course", id] for everything about a course. */
export const courseKey = (id: string, ...rest: string[]) => ["course", id, ...rest];
export const scopeKey = (scope: Scope, ...rest: string[]) =>
  scope.kind === "lecture" ? lectureKey(scope.id, ...rest) : courseKey(scope.id, ...rest);
const path = (id: string) => ({ params: { path: { lecture_id: id } } });

/** Presigned storage URLs last an hour: refetching sooner only makes images load again. */
const PRESIGNED = 30 * 60 * 1000;
/** Results change only when a lecture is processed again, which invalidates its queries. */
const RESULTS = 10 * 60 * 1000;

export function useLectures() {
  return useQuery({
    queryKey: ["lectures"],
    queryFn: async () => unwrap(await api.GET("/v1/lectures")),
    // Poll only while something is processing, so the library shows it finish.
    refetchInterval: (query) =>
      query.state.data?.some((lecture) => lecture.status === "processing") ? 5000 : false,
  });
}

export function useLecture(id: string) {
  return useQuery({
    queryKey: key(id),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}", path(id))),
  });
}

export function useMedia(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "media"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/media", path(id))),
    enabled,
    staleTime: PRESIGNED,
  });
}

export function useTranscript(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "transcript"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/transcript", path(id))),
    enabled,
    staleTime: RESULTS,
  });
}

export function useSlides(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "slides"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/slides", path(id))),
    enabled,
    staleTime: PRESIGNED,
  });
}

export function useNotes(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "notes"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/notes", path(id))),
    enabled,
    staleTime: RESULTS,
  });
}

/** A lecture's processing runs, newest first, with each stage's time and the LLM usage. */
export function useRuns(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "runs"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/runs", path(id))),
    enabled,
  });
}

/** Whether the API answers, and whether its database and storage do. */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, response } = await api.GET("/readyz");
      return { ok: response.ok, checks: (data ?? {}) as Record<string, string> };
    },
    refetchInterval: (query) => (query.state.status === "error" || !query.state.data?.ok ? 5000 : 30_000),
    retry: false,
  });
}

export const meKey = ["me"];

/** Who the API takes the viewer to be and what their quotas leave today. Refreshed after each
 *  question and upload, which use them up. */
export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => unwrap(await api.GET("/v1/me")),
    staleTime: 30_000,
  });
}

export function useCourses() {
  return useQuery({
    queryKey: ["courses"],
    queryFn: async () => unwrap(await api.GET("/v1/courses")),
  });
}

export function useCourse(id: string) {
  return useQuery({
    queryKey: courseKey(id),
    queryFn: async () => unwrap(await api.GET("/v1/courses/{course_id}", { params: { path: { course_id: id } } })),
  });
}

/** Search a lecture or a course. Cached per query until the lecture is processed again or the
 *  course changes (their keys are invalidated then). */
export function useSearch(scope: Scope, query: string) {
  const params =
    scope.kind === "lecture"
      ? { q: query, lecture_id: [scope.id], limit: 6 }
      : { q: query, course_id: scope.id, limit: 8 };
  return useQuery({
    queryKey: scopeKey(scope, "search", query),
    queryFn: async () => unwrap(await api.GET("/v1/search", { params: { query: params } })),
    enabled: query.length > 0,
    staleTime: Infinity,
  });
}

/** The viewer's own Q&A threads about a lecture or course, most recent first (all of them for
 *  an admin). Listing them needs sign-in. */
export function useThreads(scope: Scope, enabled = true) {
  return useQuery({
    queryKey: scopeKey(scope, "threads"),
    queryFn: async () =>
      scope.kind === "lecture"
        ? unwrap(await api.GET("/v1/lectures/{lecture_id}/threads", path(scope.id)))
        : unwrap(await api.GET("/v1/courses/{course_id}/threads", { params: { path: { course_id: scope.id } } })),
    enabled,
  });
}

export const threadKey = (threadId: string | null) => ["thread", threadId];

export function useThread(threadId: string | null) {
  return useQuery({
    queryKey: threadKey(threadId),
    queryFn: async () =>
      unwrap(await api.GET("/v1/threads/{thread_id}", { params: { path: { thread_id: threadId ?? "" } } })),
    enabled: threadId !== null,
  });
}

/** Live progress while a lecture processes, from the API's server-sent events. When the run
 *  ends, every query for the lecture refreshes, so results appear without a reload. Read with
 *  fetch, since EventSource can't send the session token; a dropped connection reconnects, as
 *  EventSource would, until the run ends. */
export function useProgress(id: string, active: boolean): ProgressEvent | null {
  const [event, setEvent] = useState<ProgressEvent | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let finished = false;
    const follow = async () => {
      for (let attempt = 0; !finished && !controller.signal.aborted; attempt += 1) {
        try {
          await streamEvents<ProgressEvent>(
            `${API_URL}/v1/lectures/${id}/events`,
            { headers: { Accept: "text/event-stream" }, signal: controller.signal },
            (data) => {
              attempt = 0;
              setEvent(data);
              if (data.progress?.status !== "running") {
                finished = true;
                void queryClient.invalidateQueries({ queryKey: key(id) });
              }
            },
          );
        } catch (error) {
          // Refused (not visible, signed out): trying again won't help.
          if (controller.signal.aborted || (error instanceof ApiError && error.status < 500)) return;
        }
        if (!finished) await pause(Math.min(1000 * 2 ** attempt, 15_000), controller.signal);
      }
    };
    void follow();
    return () => controller.abort();
  }, [id, active, queryClient]);

  return event;
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}
