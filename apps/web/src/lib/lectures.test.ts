import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Course, CourseDetail, Lecture } from "./api";
import { courseKey, lectureKey } from "./queries";

const lecture = (id: string, course_id: string | null = null) => ({ id, course_id, title: id }) as Lecture;
const course = (id: string, lecture_count: number) => ({ id, lecture_count }) as Course;

describe("deleteLecture", () => {
  afterEach(() => vi.unstubAllGlobals());

  // The API client keeps the fetch it was made with, so stub it, then load the modules afresh.
  async function withFetch(response: Response) {
    const fetch = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetch);
    vi.resetModules();
    const { deleteLecture } = await import("./lectures");
    return { deleteLecture, fetch };
  }

  it("sends DELETE for the lecture", async () => {
    const { deleteLecture, fetch } = await withFetch(new Response(null, { status: 204 }));
    await deleteLecture("abc");
    const request = fetch.mock.calls[0]?.[0] as Request;
    expect(request.method).toBe("DELETE");
    expect(new URL(request.url).pathname).toBe("/v1/lectures/abc");
  });

  it("throws the API's refusal", async () => {
    const busy = new Response(JSON.stringify({ detail: "The lecture is being processed." }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    });
    const { deleteLecture } = await withFetch(busy);
    await expect(deleteLecture("abc")).rejects.toMatchObject({ status: 409, message: "The lecture is being processed." });
  });
});

describe("forgetLecture", () => {
  it("drops the lecture from every cached list and counts it out of its course", async () => {
    const { forgetLecture } = await import("./lectures");
    const queryClient = new QueryClient();
    const gone = lecture("a", "c1");
    queryClient.setQueryData(["lectures"], [gone, lecture("b")]);
    queryClient.setQueryData(["courses"], [course("c1", 2), course("c2", 5)]);
    queryClient.setQueryData(courseKey("c1"), { ...course("c1", 2), lectures: [gone, lecture("d", "c1")] });
    queryClient.setQueryData(lectureKey("a"), gone);
    queryClient.setQueryData(lectureKey("a", "notes"), {});
    queryClient.setQueryData(["search", "library", "entropy"], []);

    await forgetLecture(queryClient, gone);

    expect(queryClient.getQueryData<Lecture[]>(["lectures"])?.map((l) => l.id)).toEqual(["b"]);
    expect(queryClient.getQueryData<Course[]>(["courses"])?.map((c) => c.lecture_count)).toEqual([1, 5]);
    const detail = queryClient.getQueryData<CourseDetail>(courseKey("c1"));
    expect(detail?.lectures.map((l) => l.id)).toEqual(["d"]);
    expect(detail?.lecture_count).toBe(1);
    expect(queryClient.getQueryData(lectureKey("a"))).toBeUndefined();
    expect(queryClient.getQueryData(lectureKey("a", "notes"))).toBeUndefined();
    expect(queryClient.getQueryData(["search", "library", "entropy"])).toBeUndefined();
  });

  it("leaves courses alone for a lecture outside one, and copes with nothing cached", async () => {
    const { forgetLecture } = await import("./lectures");
    const queryClient = new QueryClient();
    queryClient.setQueryData(["courses"], [course("c1", 2)]);
    await forgetLecture(queryClient, lecture("a"));
    expect(queryClient.getQueryData<Course[]>(["courses"])?.[0]?.lecture_count).toBe(2);
    expect(queryClient.getQueryData(["lectures"])).toBeUndefined();
  });
});
