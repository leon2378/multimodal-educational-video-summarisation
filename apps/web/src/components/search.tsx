"use client";

import { SearchIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { excerpt, highlight } from "@/lib/highlight";
import { useSearch } from "@/lib/queries";
import type { Open, Scope } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { Callout } from "./common";

/** `text` with the words of `query` it contains marked, to show why a result matched. It starts
 *  near the first of them, so a result clamped to a few lines still shows one. */
export function Highlighted({ text, query }: { text: string; query: string }) {
  return highlight(excerpt(text, query), query).map((piece, i) =>
    piece.match ? (
      <mark key={i} className="rounded-sm bg-now px-0.5 text-foreground">
        {piece.text}
      </mark>
    ) : (
      <span key={i}>{piece.text}</span>
    ),
  );
}

/** A search box that searches when submitted, since each search embeds and reranks. */
export function SearchBox({
  placeholder,
  onSearch,
  autoFocus,
}: {
  placeholder: string;
  onSearch: (query: string) => void;
  autoFocus?: boolean;
}) {
  const [draft, setDraft] = useState("");
  return (
    <form
      role="search"
      className="relative"
      onSubmit={(event) => {
        event.preventDefault();
        onSearch(draft.trim());
      }}
    >
      <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="text"
        enterKeyHint="search"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={500}
        autoFocus={autoFocus}
        className="pr-8 pl-8"
      />
      {draft && (
        <button
          type="button"
          onClick={() => {
            setDraft("");
            onSearch("");
          }}
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
          aria-label="Clear the search"
        >
          <XIcon className="size-4" />
        </button>
      )}
    </form>
  );
}

/** Results for `query` in a lecture or a course. Each plays from where it starts; `titles`
 *  names the lectures in a course's results. */
export function SearchResults({
  scope,
  query,
  onOpen,
  titles = {},
}: {
  scope: Scope;
  query: string;
  onOpen: Open;
  titles?: Record<string, string>;
}) {
  const results = useSearch(scope, query);
  const where = scope.kind === "lecture" ? "this lecture" : "this course";

  if (results.isFetching && !results.data) {
    return (
      <div className="flex flex-col gap-3" aria-busy>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex flex-col gap-2 rounded-lg border p-3">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ))}
      </div>
    );
  }
  if (results.isError) {
    return (
      <Callout tone="error" title="Search failed">
        {results.error.message}
      </Callout>
    );
  }
  if (!results.data) return null;
  if (results.data.hits.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">Nothing in {where} matches “{query}”.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="px-1 text-xs text-muted-foreground">
        Best matches for “{query}”, by meaning as well as words
      </p>
      <ol className="flex flex-col gap-1.5" aria-label="Search results">
        {results.data.hits.map((hit) => {
          const lecture = scope.kind === "course" ? titles[hit.lecture_id] : undefined;
          return (
            <li key={`${hit.lecture_id}-${hit.segment_id}`}>
              <button
                type="button"
                onClick={() => onOpen(hit.lecture_id, hit.start_s)}
                aria-label={`Play ${lecture ? `${lecture} ` : ""}from ${formatTime(hit.start_s)}`}
                className="group flex w-full flex-col gap-1.5 rounded-lg border border-transparent p-3 text-left transition-colors hover:border-border hover:bg-accent/60"
              >
                <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                  <span className="rounded-md bg-brand-soft px-1.5 py-px font-mono font-medium text-brand-ink tabular-nums group-hover:bg-primary group-hover:text-primary-foreground">
                    {formatTime(hit.start_s)}
                  </span>
                  <span className="truncate">{[lecture, hit.chapter].filter(Boolean).join(" · ")}</span>
                </span>
                {hit.slide_title && <span className="text-sm font-medium">{hit.slide_title}</span>}
                <span className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                  <Highlighted text={hit.transcript} query={query} />
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
