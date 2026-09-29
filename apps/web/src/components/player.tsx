"use client";

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { type RefObject, useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { StudyNotes } from "@/lib/api";
import { formatTime } from "@/lib/timeline";

/** The chapters as a segmented bar under the video, filled up to what's playing, with the
 *  current chapter's title and buttons for the previous and next. */
export function ChapterRail({
  chapters,
  current,
  time,
  onSeek,
}: {
  chapters: StudyNotes["chapters"];
  current: number;
  time: number;
  onSeek: (seconds: number) => void;
}) {
  const chapter = chapters[current];
  // Like a media player: "previous" restarts the chapter unless it has only just begun.
  const previous = () => {
    if (chapter && time - chapter.start_s > 3) onSeek(chapter.start_s);
    else if (current > 0) onSeek(chapters[current - 1]?.start_s ?? 0);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-2.5 items-center gap-[3px]" role="group" aria-label="Chapters">
        {chapters.map((c, i) => {
          const span = Math.max(c.end_s - c.start_s, 1);
          const fill = i < current ? 100 : i > current ? 0 : Math.min(100, Math.max(0, ((time - c.start_s) / span) * 100));
          return (
            <Tooltip key={c.start_s}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => onSeek(c.start_s)}
                  aria-label={`Chapter ${i + 1}: ${c.title}, from ${formatTime(c.start_s)}`}
                  style={{ flexGrow: span }}
                  className="relative h-1.5 min-w-1.5 basis-0 overflow-hidden rounded-full bg-muted-foreground/20 transition-[height] hover:h-2.5 focus-visible:h-2.5 focus-visible:outline-none"
                >
                  <span className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${fill}%` }} />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {i + 1}. {c.title} · {formatTime(c.start_s)}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground tabular-nums">
            {current >= 0 ? `Chapter ${current + 1} of ${chapters.length}` : `${chapters.length} chapters`}
          </p>
          <p className="truncate font-medium">{chapter?.title ?? chapters[0]?.title}</p>
        </div>
        <Button variant="outline" size="icon-sm" onClick={previous} disabled={current < 0} aria-label="Previous chapter">
          <ChevronLeftIcon />
        </Button>
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onSeek(chapters[current + 1]?.start_s ?? 0)}
          disabled={current >= chapters.length - 1}
          aria-label="Next chapter"
        >
          <ChevronRightIcon />
        </Button>
      </div>
    </div>
  );
}

/** YouTube-style keys for the lecture's video: K or space to play and pause, J and L for ten
 *  seconds back and forward, the arrows for five. Ignored while typing, in a dialog, on tabs
 *  (which use the arrows) and on the video itself (its controls handle keys already). */
export function usePlayerKeys(video: RefObject<HTMLVideoElement | null>) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select, video, [contenteditable], [role=dialog], [role=tablist], [role=menu]")) {
        return;
      }
      const player = video.current;
      if (!player) return;
      // Browsers may refuse to play without a click on the page; the key then does nothing.
      const toggle = () => (player.paused ? void player.play().catch(() => undefined) : player.pause());
      const skip = (seconds: number) => {
        player.currentTime = Math.max(0, Math.min(player.duration || Infinity, player.currentTime + seconds));
      };
      switch (event.key) {
        case " ":
          if (target.closest("button, a")) return;
          toggle();
          break;
        case "k":
          toggle();
          break;
        case "j":
          skip(-10);
          break;
        case "l":
          skip(10);
          break;
        case "ArrowLeft":
          skip(-5);
          break;
        case "ArrowRight":
          skip(5);
          break;
        default:
          return;
      }
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [video]);
}

export function KeyHints() {
  return (
    <p className="hidden items-center gap-1.5 text-xs text-muted-foreground lg:flex">
      <Kbd>K</Kbd> play or pause <span className="mx-1">·</span>
      <Kbd>J</Kbd>
      <Kbd>L</Kbd> 10 s back or on <span className="mx-1">·</span>
      <Kbd>←</Kbd>
      <Kbd>→</Kbd> 5 s
    </p>
  );
}
