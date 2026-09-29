"use client";

import { useState } from "react";

import { useSearch } from "@/lib/queries";
import type { Open, Scope } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

/** Search a lecture, or every lecture in a course. Each hit plays from where it starts;
 *  `titles` names the lectures in a course's results. */
export function SearchPanel({
  scope,
  onOpen,
  titles = {},
}: {
  scope: Scope;
  onOpen: Open;
  titles?: Record<string, string>;
}) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const results = useSearch(scope, query);
  const where = scope.kind === "lecture" ? "this lecture" : "this course";

  return (
    <div className="flex flex-col gap-3 text-sm">
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <input
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={`Search ${where}`}
          aria-label={`Search ${where}`}
          maxLength={500}
          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 dark:border-slate-700 dark:bg-slate-950"
        />
        <button
          type="submit"
          disabled={!draft.trim()}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Search
        </button>
      </form>
      {results.isFetching && <p className="text-slate-500">Searching…</p>}
      {results.isError && (
        <p className="text-rose-600" role="alert">
          {results.error.message}
        </p>
      )}
      {results.data?.hits.length === 0 && <p className="text-slate-500">Nothing in {where} matches.</p>}
      {results.data && results.data.hits.length > 0 && (
        <ol className="flex flex-col gap-1" aria-label="Search results">
          {results.data.hits.map((hit) => {
            const lecture = scope.kind === "course" ? titles[hit.lecture_id] : undefined;
            return (
              <li key={`${hit.lecture_id}-${hit.segment_id}`}>
                <button
                  type="button"
                  onClick={() => onOpen(hit.lecture_id, hit.start_s)}
                  aria-label={`Play ${lecture ? `${lecture} ` : ""}from ${formatTime(hit.start_s)}`}
                  className="w-full rounded-lg p-2 text-left hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  <span className="flex items-baseline gap-2 text-xs text-slate-500">
                    <span className="font-mono text-indigo-700 dark:text-indigo-300">[{formatTime(hit.start_s)}]</span>
                    <span className="truncate">{[lecture, hit.chapter].filter(Boolean).join(" · ")}</span>
                  </span>
                  {hit.slide_title && <span className="block font-medium">{hit.slide_title}</span>}
                  <span className="line-clamp-3 leading-relaxed text-slate-700 dark:text-slate-300">{hit.transcript}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
