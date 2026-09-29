"use client";

import { cn } from "cn";
import { LocateFixedIcon } from "lucide-react";
import { Fragment, type RefObject, memo, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { StudyNotes, TranscriptLine } from "@/lib/api";
import type { Open, Scope } from "@/lib/scope";
import { formatTime, indexAt } from "@/lib/timeline";

import { scrollWithin } from "./common";
import { SearchBox, SearchResults } from "./search";

type Chapters = StudyNotes["chapters"];

/** The transcript, following the video, with search over the lecture in the same place.
 *  `current` is the index of the line playing. */
export const TranscriptPanel = memo(function TranscriptPanel({
  lectureId,
  lines,
  chapters,
  current,
  visible,
  onSeek,
  onOpen,
}: {
  lectureId: string;
  lines: TranscriptLine[];
  chapters: Chapters;
  current: number;
  /** Whether its tab is showing: a hidden list can't be scrolled, so it catches up on showing. */
  visible: boolean;
  onSeek: (seconds: number) => void;
  onOpen: Open;
}) {
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);
  const active = useRef<HTMLButtonElement>(null);
  const scope: Scope = { kind: "lecture", id: lectureId };

  const shown = useRef(false);
  useEffect(() => {
    if (!visible) {
      shown.current = false;
      return;
    }
    // Jump straight there on showing the tab; glide while it's open.
    if (follow && !query) scrollWithin(scroller.current, active.current, shown.current);
    shown.current = true;
  }, [current, follow, query, visible]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b p-3">
        <SearchBox placeholder="Search this lecture" onSearch={setQuery} />
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          ref={scroller}
          className="h-full overflow-y-auto"
          // Scrolling by hand stops following, until "Back to now".
          onWheel={() => setFollow(false)}
          onTouchMove={() => setFollow(false)}
        >
          {query ? (
            <div className="p-3">
              <SearchResults scope={scope} query={query} onOpen={onOpen} />
            </div>
          ) : lines.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">No speech was transcribed.</p>
          ) : (
            <Lines lines={lines} chapters={chapters} current={current} onSeek={onSeek} active={active} />
          )}
        </div>
        {!query && !follow && current >= 0 && (
          <Button
            size="sm"
            variant="secondary"
            className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border shadow-md"
            onClick={() => setFollow(true)}
          >
            <LocateFixedIcon /> Back to now
          </Button>
        )}
      </div>
    </div>
  );
});

/** Every line, with a heading where each chapter starts. Memoised on `current`, so playback
 *  doesn't re-render hundreds of lines on every tick. */
const Lines = memo(function Lines({
  lines,
  chapters,
  current,
  onSeek,
  active,
}: {
  lines: TranscriptLine[];
  chapters: Chapters;
  current: number;
  onSeek: (seconds: number) => void;
  active: RefObject<HTMLButtonElement | null>;
}) {
  let chapter = -1;
  return (
    <ol className="flex flex-col py-2">
      {lines.map((line, index) => {
        const inChapter = indexAt(chapters, line.start_s + 0.01);
        const heading = inChapter !== chapter && inChapter >= 0 ? chapters[inChapter] : undefined;
        chapter = inChapter;
        const playing = index === current;
        return (
          <Fragment key={line.index}>
            {heading && (
              <li className="px-4 pt-5 pb-1.5 text-xs font-semibold tracking-wider text-muted-foreground uppercase first:pt-2">
                {inChapter + 1}. {heading.title}
              </li>
            )}
            <li>
              <button
                ref={playing ? active : undefined}
                type="button"
                onClick={() => onSeek(line.start_s)}
                aria-current={playing || undefined}
                className={cn(
                  "grid w-full grid-cols-[3.25rem_1fr] gap-2 px-4 py-1.5 text-left text-sm leading-relaxed transition-colors",
                  playing ? "bg-now shadow-[inset_3px_0_0_var(--now-ink)]" : "hover:bg-accent",
                )}
              >
                <span
                  className={cn(
                    "pt-px font-mono text-xs tabular-nums",
                    playing ? "text-now-ink" : "text-muted-foreground",
                  )}
                >
                  {formatTime(line.start_s)}
                </span>
                <span>{line.text}</span>
              </button>
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
});
