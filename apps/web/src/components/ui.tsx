"use client";

import katex from "katex";
import { useMemo } from "react";

import type { LectureStatus } from "@/lib/api";
import { formatTime } from "@/lib/timeline";

const STATUS: Record<LectureStatus, { label: string; className: string }> = {
  awaiting_upload: { label: "Waiting for upload", className: "bg-slate-200 text-slate-700" },
  uploaded: { label: "Uploaded", className: "bg-sky-100 text-sky-800" },
  processing: { label: "Processing", className: "bg-amber-100 text-amber-800" },
  ready: { label: "Ready", className: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Failed", className: "bg-rose-100 text-rose-800" },
};

export function StatusBadge({ status }: { status: LectureStatus }) {
  const { label, className } = STATUS[status];
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}

/** A citation like [12:30]: jumps the video there. */
export function TimeButton({ seconds, onSeek }: { seconds: number; onSeek: (t: number) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      className="rounded px-1 font-mono text-sm text-indigo-700 hover:bg-indigo-50 hover:underline dark:text-indigo-300 dark:hover:bg-indigo-950"
      aria-label={`Play from ${formatTime(seconds)}`}
    >
      [{formatTime(seconds)}]
    </button>
  );
}

/** LaTeX from the notes, rendered by KaTeX. The LaTeX comes from an LLM reading slides, so it's
 *  untrusted: KaTeX escapes its input, and with `trust` off it refuses \href, \url and raw HTML. */
export function Latex({ source }: { source: string }) {
  const html = useMemo(
    () =>
      katex.renderToString(source, { throwOnError: false, displayMode: true, trust: false }),
    [source],
  );
  return <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Card({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {title && <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>}
      {children}
    </section>
  );
}
