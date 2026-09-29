"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { api, unwrap } from "@/lib/api";
import { useCourses, useLectures } from "@/lib/queries";
import { formatTime } from "@/lib/timeline";

import { Card, StatusBadge } from "./ui";

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading"; percent: number }
  | { phase: "starting" }
  | { phase: "error"; message: string };

interface UploadTarget {
  method: string;
  url: string;
  headers: Record<string, string>;
}

/** Upload a lecture straight to storage, then start processing it. */
export function UploadForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const courses = useCourses();
  const [title, setTitle] = useState("");
  const [courseId, setCourseId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<UploadState>({ phase: "idle" });
  const busy = state.phase === "uploading" || state.phase === "starting";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    try {
      setState({ phase: "uploading", percent: 0 });
      const created = unwrap(
        await api.POST("/v1/lectures", {
          body: {
            title: title.trim() || file.name,
            filename: file.name,
            content_type: file.type || "video/mp4",
            course_id: courseId || null,
          },
        }),
      );
      await putWithProgress(created.upload, file, (percent) =>
        setState({ phase: "uploading", percent }),
      );
      const params = { params: { path: { lecture_id: created.lecture.id } } };
      unwrap(await api.POST("/v1/lectures/{lecture_id}/complete-upload", params));
      setState({ phase: "starting" });
      unwrap(await api.POST("/v1/lectures/{lecture_id}/process", params));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
      ]);
      router.push(`/lectures/${created.lecture.id}`);
    } catch (error) {
      setState({ phase: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  return (
    <Card title="Add a lecture">
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <label className="flex flex-1 flex-col gap-1 text-sm">
          Title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Defaults to the file name"
            className="rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
          />
        </label>
        {courses.data && courses.data.length > 0 && (
          <label className="flex flex-col gap-1 text-sm">
            Course
            <select
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              className="rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            >
              <option value="">None</option>
              {courses.data.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-1 flex-col gap-1 text-sm">
          Video
          <input
            type="file"
            accept="video/*"
            required
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 dark:file:bg-slate-800"
          />
        </label>
        <button
          type="submit"
          disabled={busy || !file}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Upload and process
        </button>
      </form>
      {state.phase === "uploading" && (
        <div className="mt-3" role="status">
          <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div className="h-full bg-indigo-600 transition-all" style={{ width: `${state.percent}%` }} />
          </div>
          <p className="mt-1 text-sm text-slate-500">Uploading… {state.percent}%</p>
        </div>
      )}
      {state.phase === "starting" && <p className="mt-3 text-sm text-slate-500">Starting processing…</p>}
      {state.phase === "error" && (
        <p className="mt-3 text-sm text-rose-600" role="alert">
          {state.message}
        </p>
      )}
    </Card>
  );
}

/** PUT with upload progress, which fetch can't report. Headers must match what the URL was
 *  signed with. */
function putWithProgress(target: UploadTarget, file: File, onProgress: (percent: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(target.method, target.url);
    for (const [name, value] of Object.entries(target.headers)) request.setRequestHeader(name, value);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(new Error(`Upload failed: HTTP ${request.status}`));
    request.onerror = () => reject(new Error("Upload failed: couldn't reach storage."));
    request.send(file);
  });
}

/** Courses group lectures, so search and Q&A can span them. */
export function CourseList() {
  const queryClient = useQueryClient();
  const courses = useCourses();
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      unwrap(await api.POST("/v1/courses", { body: { title: title.trim() } }));
      setTitle("");
      await queryClient.invalidateQueries({ queryKey: ["courses"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card title="Courses">
      {courses.data && courses.data.length > 0 && (
        <ul className="mb-3 divide-y divide-slate-100 dark:divide-slate-800">
          {courses.data.map((course) => (
            <li key={course.id} className="flex items-center gap-3 py-2">
              <Link href={`/courses/${course.id}`} className="flex-1 font-medium hover:underline">
                {course.title}
              </Link>
              <span className="text-sm text-slate-500">
                {course.lecture_count} {course.lecture_count === 1 ? "lecture" : "lectures"}
              </span>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={create} className="flex gap-2 text-sm">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New course, e.g. MIT 6.0001 Fall 2016"
          aria-label="New course title"
          maxLength={300}
          className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
        />
        <button
          type="submit"
          disabled={!title.trim()}
          className="rounded-lg border border-slate-300 px-3 py-2 font-medium hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          Create
        </button>
      </form>
      {error && (
        <p className="mt-2 text-sm text-rose-600" role="alert">
          {error}
        </p>
      )}
    </Card>
  );
}

export function LectureList() {
  const lectures = useLectures();
  const courses = useCourses();
  const courseTitle = (id: string | null) => courses.data?.find((course) => course.id === id)?.title;

  if (lectures.isPending) return <p className="text-sm text-slate-500">Loading lectures…</p>;
  if (lectures.isError) {
    return (
      <p className="text-sm text-rose-600" role="alert">
        Couldn&apos;t load lectures: {lectures.error.message}
      </p>
    );
  }
  if (lectures.data.length === 0) {
    return <p className="text-sm text-slate-500">No lectures yet. Upload one above.</p>;
  }
  return (
    <Card title="Lectures">
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {lectures.data.map((lecture) => (
          <li key={lecture.id} className="flex items-center gap-3 py-2">
            <Link href={`/lectures/${lecture.id}`} className="flex-1 font-medium hover:underline">
              {lecture.title}
            </Link>
            {courseTitle(lecture.course_id) && (
              <span className="truncate text-sm text-slate-500">{courseTitle(lecture.course_id)}</span>
            )}
            {lecture.duration_s != null && (
              <span className="font-mono text-sm text-slate-500">{formatTime(lecture.duration_s)}</span>
            )}
            <StatusBadge status={lecture.status} />
          </li>
        ))}
      </ul>
    </Card>
  );
}
