"use client";

import { cn } from "cn";
import { CheckIcon, CopyIcon, DownloadIcon, EyeIcon, RotateCcwIcon, SparklesIcon } from "lucide-react";
import { memo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { StudyNotes } from "@/lib/api";
import { notesToMarkdown, slugify } from "@/lib/notes";

import { Latex, SectionLabel, TimeChip, useStoredState } from "./common";

type Seek = (seconds: number) => void;

export function downloadNotes(notes: StudyNotes, title: string) {
  const blob = new Blob([notesToMarkdown(notes, title)], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${slugify(title)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

/** The summary, chapters (the one playing marked), key concepts and formulas. */
export const NotesPanel = memo(function NotesPanel({
  notes,
  title,
  currentChapter,
  onSeek,
}: {
  notes: StudyNotes;
  title: string;
  currentChapter: number;
  onSeek: Seek;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(notesToMarkdown(notes, title));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="flex flex-col gap-8 p-5">
      <section className="relative rounded-xl border border-primary/15 bg-brand-soft/60 p-4">
        <div className="mb-2 flex items-center gap-1.5">
          <p className="flex items-center gap-1.5 text-xs font-semibold tracking-wider text-brand-ink uppercase">
            <SparklesIcon className="size-3.5" /> In short
          </p>
          <div className="ml-auto flex">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-xs" onClick={() => void copy()} aria-label="Copy the notes as Markdown">
                  {copied ? <CheckIcon /> : <CopyIcon />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Copy as Markdown</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => downloadNotes(notes, title)}
                  aria-label="Download the notes as Markdown"
                >
                  <DownloadIcon />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Download as Markdown</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <p className="leading-relaxed">{notes.tldr}</p>
      </section>

      <section>
        <SectionLabel count={notes.chapters.length}>Chapters</SectionLabel>
        <ol className="-mx-2 flex flex-col gap-0.5">
          {notes.chapters.map((chapter, index) => {
            const playing = index === currentChapter;
            return (
              <li
                key={chapter.start_s}
                aria-current={playing || undefined}
                className={cn("flex gap-3 rounded-lg p-2 transition-colors", playing && "bg-now")}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium tabular-nums",
                    playing
                      ? "bg-now-ink text-background"
                      : index < currentChapter
                        ? "bg-muted text-muted-foreground"
                        : "border text-muted-foreground",
                  )}
                >
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <button
                      type="button"
                      onClick={() => onSeek(chapter.start_s)}
                      className="text-left font-medium hover:text-primary"
                    >
                      {chapter.title}
                    </button>
                    <TimeChip seconds={chapter.start_s} onSeek={onSeek} />
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{chapter.summary}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section>
        <SectionLabel count={notes.concepts.length}>Key concepts</SectionLabel>
        <dl className="flex flex-col divide-y">
          {notes.concepts.map((concept, index) => (
            <div key={index} className="py-3 first:pt-0 last:pb-0">
              <dt className="flex flex-wrap items-center gap-2 font-medium">
                {concept.term} <TimeChip seconds={concept.at_s} onSeek={onSeek} />
              </dt>
              <dd className="mt-1 text-sm leading-relaxed text-muted-foreground">{concept.definition}</dd>
            </div>
          ))}
        </dl>
      </section>

      {notes.formulas.length > 0 && (
        <section>
          <SectionLabel count={notes.formulas.length}>Formulas</SectionLabel>
          <ul className="flex flex-col gap-3">
            {notes.formulas.map((formula, index) => (
              <li key={index} className="rounded-lg border bg-muted/30 px-4 py-3">
                <Latex source={formula.latex} />
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  {formula.meaning} <TimeChip seconds={formula.at_s} onSeek={onSeek} />
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
});

type Mark = "knew" | "review";

/** Questions to test yourself, answers hidden until asked for. What you mark is remembered in
 *  this browser, per lecture. */
export const QuizPanel = memo(function QuizPanel({
  lectureId,
  quiz,
  onSeek,
}: {
  lectureId: string;
  quiz: StudyNotes["quiz"];
  onSeek: Seek;
}) {
  const [marks, setMarks] = useStoredState<Record<number, Mark>>(`quiz:${lectureId}`, {});
  const [shown, setShown] = useState<Set<number>>(new Set());
  const top = useRef<HTMLDivElement>(null);
  const knew = Object.values(marks).filter((mark) => mark === "knew").length;
  const review = Object.values(marks).filter((mark) => mark === "review").length;
  const total = Math.max(quiz.length, 1);

  const mark = (index: number, value: Mark) => setMarks({ ...marks, [index]: value });
  const reset = () => {
    setMarks({});
    setShown(new Set());
    top.current?.scrollIntoView({ block: "nearest" });
  };

  if (quiz.length === 0) return <p className="p-6 text-center text-sm text-muted-foreground">No quiz for this lecture.</p>;

  return (
    <div ref={top} className="flex flex-col gap-4 p-5">
      <div className="flex flex-col gap-2 rounded-xl border bg-muted/30 p-4">
        <div className="flex items-center gap-2">
          <p className="text-sm">
            <span className="font-semibold tabular-nums">{knew + review}</span>
            <span className="text-muted-foreground"> of {quiz.length} answered</span>
          </p>
          {knew + review > 0 && (
            <Button variant="ghost" size="xs" className="ml-auto" onClick={reset}>
              <RotateCcwIcon /> Start over
            </Button>
          )}
        </div>
        <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="bg-success transition-all" style={{ width: `${(knew / total) * 100}%` }} />
          <div className="bg-warning transition-all" style={{ width: `${(review / total) * 100}%` }} />
        </div>
        <p className="flex gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-success" /> Knew {knew}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-warning" /> To review {review}
          </span>
        </p>
      </div>

      <ol className="flex flex-col gap-3">
        {quiz.map((question, index) => {
          const marked = marks[index];
          const open = shown.has(index) || marked !== undefined;
          return (
            <li
              key={index}
              className={cn(
                "rounded-xl border border-l-4 bg-card p-4 transition-colors",
                marked === "knew" ? "border-l-success" : marked === "review" ? "border-l-warning" : "border-l-border",
              )}
            >
              <p className="text-xs font-medium text-muted-foreground">Question {index + 1}</p>
              <p className="mt-1 leading-relaxed font-medium">{question.question}</p>
              {open ? (
                <>
                  <div className="mt-3 rounded-lg bg-muted/50 p-3 text-sm leading-relaxed animate-in fade-in-0">
                    {question.answer} <TimeChip seconds={question.at_s} onSeek={onSeek} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant={marked === "knew" ? "default" : "outline"}
                      className={cn(marked === "knew" && "bg-success hover:bg-success/90")}
                      aria-pressed={marked === "knew"}
                      onClick={() => mark(index, "knew")}
                    >
                      <CheckIcon /> I knew it
                    </Button>
                    <Button
                      size="sm"
                      variant={marked === "review" ? "default" : "outline"}
                      className={cn(marked === "review" && "bg-warning text-black hover:bg-warning/90")}
                      aria-pressed={marked === "review"}
                      onClick={() => mark(index, "review")}
                    >
                      <RotateCcwIcon /> Review later
                    </Button>
                  </div>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-3"
                  onClick={() => setShown(new Set(shown).add(index))}
                >
                  <EyeIcon /> Show answer
                </Button>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
});
