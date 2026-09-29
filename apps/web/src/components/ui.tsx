"use client";

import katex from "katex";
import { useMemo, useState } from "react";

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

/** A citation like [12:30]: jumps the video there. `label` replaces the text shown, e.g.
 *  "[L2 12:30]", and `lecture` names the lecture it plays. */
export function TimeButton({
  seconds,
  onSeek,
  label,
  lecture,
}: {
  seconds: number;
  onSeek: (t: number) => void;
  label?: string;
  lecture?: string | null;
}) {
  const where = lecture ? `${lecture} from ${formatTime(seconds)}` : `from ${formatTime(seconds)}`;
  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      className="rounded px-1 font-mono text-sm text-indigo-700 hover:bg-indigo-50 hover:underline dark:text-indigo-300 dark:hover:bg-indigo-950"
      aria-label={`Play ${where}`}
      title={lecture ?? undefined}
    >
      {label ?? `[${formatTime(seconds)}]`}
    </button>
  );
}

/** A panel of tabs; `render` draws the selected one. */
export function Tabs<T extends string>({
  tabs,
  render,
  className = "",
}: {
  tabs: [T, string][];
  render: (tab: T) => React.ReactNode;
  className?: string;
}) {
  const [first] = tabs;
  const [tab, setTab] = useState<T | undefined>(first?.[0]);
  return (
    <section
      className={`flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
    >
      <div role="tablist" className="flex border-b border-slate-200 dark:border-slate-800">
        {tabs.map(([value, label]) => (
          <button
            key={value}
            role="tab"
            type="button"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={`flex-1 px-3 py-2 text-sm font-medium ${
              tab === value ? "border-b-2 border-indigo-600 text-indigo-700 dark:text-indigo-300" : "text-slate-500"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="overflow-y-auto p-4">
        {tab !== undefined && render(tab)}
      </div>
    </section>
  );
}

/** LaTeX from the notes or an answer, rendered by KaTeX. It comes from an LLM, so it's untrusted:
 *  KaTeX escapes its input, and with `trust` off it refuses \href, \url and raw HTML. */
export function Latex({ source, inline = false }: { source: string; inline?: boolean }) {
  const html = useMemo(
    () =>
      katex.renderToString(source, { throwOnError: false, displayMode: !inline, trust: false }),
    [source, inline],
  );
  return inline ? (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

export function Card({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {title && <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>}
      {children}
    </section>
  );
}
