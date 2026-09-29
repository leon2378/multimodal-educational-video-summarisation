"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { useCourse } from "@/lib/queries";
import { type Open, lectureHref } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { ChatPanel } from "./chat";
import { SearchPanel } from "./search";
import { Card, StatusBadge, Tabs } from "./ui";

/** A course: its lectures, then search and Q&A across all of them. Results and citations open
 *  the lecture they point into, from that moment. */
export function CourseView({ id }: { id: string }) {
  const router = useRouter();
  const course = useCourse(id);
  const open = useCallback<Open>((lectureId, seconds) => router.push(lectureHref(lectureId, seconds)), [router]);

  if (course.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (course.isError) {
    return (
      <p className="text-rose-600" role="alert">
        {course.error.message}
      </p>
    );
  }
  const info = course.data;
  const titles = Object.fromEntries(info.lectures.map((lecture) => [lecture.id, lecture.title]));
  const scope = { kind: "course", id } as const;
  const searchable = info.lectures.some((lecture) => lecture.status === "ready");

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Link href="/" className="text-sm text-slate-500 hover:underline">
          ← Library
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{info.title}</h1>
        {info.description && <p className="mt-1 text-slate-600 dark:text-slate-400">{info.description}</p>}
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <Card title={`Lectures (${info.lecture_count})`}>
            {info.lectures.length === 0 ? (
              <p className="text-sm text-slate-500">
                No lectures yet. Choose this course when uploading a lecture, or on a lecture&apos;s page.
              </p>
            ) : (
              <ol className="divide-y divide-slate-100 dark:divide-slate-800">
                {info.lectures.map((lecture) => (
                  <li key={lecture.id} className="flex items-center gap-3 py-2">
                    <Link href={lectureHref(lecture.id)} className="flex-1 font-medium hover:underline">
                      {lecture.title}
                    </Link>
                    {lecture.duration_s != null && (
                      <span className="font-mono text-sm text-slate-500">{formatTime(lecture.duration_s)}</span>
                    )}
                    <StatusBadge status={lecture.status} />
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
        <div className="lg:col-span-3">
          {searchable ? (
            <Tabs
              className="max-h-[calc(100vh-7rem)] lg:sticky lg:top-4"
              tabs={[
                ["ask", "Ask the course"],
                ["search", "Search the course"],
              ]}
              render={(tab) =>
                tab === "ask" ? (
                  <ChatPanel scope={scope} onOpen={open} />
                ) : (
                  <SearchPanel scope={scope} onOpen={open} titles={titles} />
                )
              }
            />
          ) : (
            <Card>
              <p className="text-sm text-slate-500">
                Search and Q&amp;A across the course start once one of its lectures has been processed.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
