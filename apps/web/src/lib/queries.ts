"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_URL, type ProgressEvent, api, unwrap } from "./api";
import type { Scope } from "./scope";

/** Every query for a lecture starts with ["lecture", id], so one invalidation refreshes it all. */
export const lectureKey = (id: string, ...rest: string[]) => ["lecture", id, ...rest];
const key = lectureKey;
/** Likewise ["course", id] for everything about a course. */
export const courseKey = (id: string, ...rest: string[]) => ["course", id, ...rest];
export const scopeKey = (scope: Scope, ...rest: string[]) =>
  scope.kind === "lecture" ? lectureKey(scope.id, ...rest) : courseKey(scope.id, ...rest);
const path = (id: string) => ({ params: { path: { lecture_id: id } } });

export function useLectures() {
  return useQuery({
    queryKey: ["lectures"],
    queryFn: async () => unwrap(await api.GET("/v1/lectures")),
    refetchInterval: 5000,
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
    // The presigned URL lasts an hour; refresh it well before then.
    staleTime: 30 * 60 * 1000,
  });
}

export function useTranscript(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "transcript"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/transcript", path(id))),
    enabled,
  });
}

export function useSlides(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "slides"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/slides", path(id))),
    enabled,
  });
}

export function useNotes(id: string, enabled: boolean) {
  return useQuery({
    queryKey: key(id, "notes"),
    queryFn: async () => unwrap(await api.GET("/v1/lectures/{lecture_id}/notes", path(id))),
    enabled,
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

/** A lecture's or course's Q&A threads, most recent first. */
export function useThreads(scope: Scope) {
  return useQuery({
    queryKey: scopeKey(scope, "threads"),
    queryFn: async () =>
      scope.kind === "lecture"
        ? unwrap(await api.GET("/v1/lectures/{lecture_id}/threads", path(scope.id)))
        : unwrap(await api.GET("/v1/courses/{course_id}/threads", { params: { path: { course_id: scope.id } } })),
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
 *  ends, every query for the lecture refreshes, so results appear without a reload. */
export function useProgress(id: string, active: boolean): ProgressEvent | null {
  const [event, setEvent] = useState<ProgressEvent | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!active) return;
    // A dropped connection reconnects on its own; the stream is closed here once the run ends.
    const source = new EventSource(`${API_URL}/v1/lectures/${id}/events`);
    source.onmessage = (message: MessageEvent<string>) => {
      const data = JSON.parse(message.data) as ProgressEvent;
      setEvent(data);
      if (data.progress?.status !== "running") {
        source.close();
        void queryClient.invalidateQueries({ queryKey: key(id) });
      }
    };
    return () => source.close();
  }, [id, active, queryClient]);

  return event;
}
