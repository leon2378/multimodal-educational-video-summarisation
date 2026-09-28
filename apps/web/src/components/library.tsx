"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { api, unwrap } from "@/lib/api";
import { useLectures } from "@/lib/queries";
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
  const [title, setTitle] = useState("");
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
      await queryClient.invalidateQueries({ queryKey: ["lectures"] });
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

export function LectureList() {
  const lectures = useLectures();

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
