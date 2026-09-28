"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { type ProgressEvent, type Slide, type StudyNotes, type TranscriptLine, api, unwrap } from "@/lib/api";
import { useLecture, useMedia, useNotes, useProgress, useSlides, useTranscript } from "@/lib/queries";
import { indexAt, slideAt } from "@/lib/timeline";

import { Card, Latex, StatusBadge, TimeButton } from "./ui";

type Seek = (seconds: number) => void;

export function LectureView({ id }: { id: string }) {
  const lecture = useLecture(id);
  const status = lecture.data?.status;
  const ready = status === "ready";
  // The event stream also reports a finished run once, which is how a failed run's error shows.
  const progress = useProgress(id, status === "processing" || status === "failed");
  const media = useMedia(id, status !== undefined && status !== "awaiting_upload");
  const transcript = useTranscript(id, ready);
  const slides = useSlides(id, ready);
  const notes = useNotes(id, ready);

  const video = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const seek = useCallback<Seek>((seconds) => {
    const player = video.current;
    if (!player) return;
    player.currentTime = seconds;
    void player.play();
  }, []);

  if (lecture.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (lecture.isError) {
    return (
      <p className="text-rose-600" role="alert">
        {lecture.error.message}
      </p>
    );
  }
  const info = lecture.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{info.title}</h1>
        <StatusBadge status={info.status} />
        {(info.status === "uploaded" || info.status === "failed") && <ProcessButton id={id} />}
      </div>
      {(info.licence || info.attribution) && (
        <p className="text-sm text-slate-500">{[info.attribution, info.licence].filter(Boolean).join(" · ")}</p>
      )}
      {(status === "processing" || status === "failed") && <ProcessingPanel event={progress} />}

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="flex flex-col gap-4 lg:col-span-3">
          <div className="overflow-hidden rounded-xl bg-black">
            {media.data ? (
              <video
                ref={video}
                src={media.data.url}
                controls
                preload="metadata"
                className="aspect-video w-full"
                onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
              />
            ) : (
              <div className="flex aspect-video items-center justify-center text-sm text-slate-400">
                {status === "awaiting_upload" ? "Waiting for the upload" : "Loading video…"}
              </div>
            )}
          </div>
          {slides.data && slides.data.length > 0 && (
            <SlideStrip slides={slides.data} current={slideAt(slides.data, time)} onSeek={seek} />
          )}
          {notes.data && <Chapters notes={notes.data.notes} time={time} onSeek={seek} />}
        </div>
        <div className="lg:col-span-2">
          {ready ? (
            <SidePanel
              transcript={transcript.data ?? []}
              notes={notes.data?.notes}
              time={time}
              onSeek={seek}
            />
          ) : (
            <Card>
              <p className="text-sm text-slate-500">
                The transcript, notes and quiz appear here once processing finishes.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function ProcessButton({ id }: { id: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      unwrap(await api.POST("/v1/lectures/{lecture_id}/process", { params: { path: { lecture_id: id } } }));
      await queryClient.invalidateQueries({ queryKey: ["lecture", id] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      <button
        type="button"
        onClick={start}
        disabled={busy}
        className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
      >
        Process
      </button>
      {error && <span className="text-sm text-rose-600">{error}</span>}
    </span>
  );
}

const STAGES: [string, string][] = [
  ["probe", "Reading the video"],
  ["audio", "Extracting audio"],
  ["asr", "Transcribing"],
  ["slides", "Finding slides"],
  ["read_slides", "Reading slides"],
  ["timeline", "Building the timeline"],
  ["chapters", "Planning chapters"],
  ["draft_notes", "Writing notes"],
  ["notes", "Assembling notes"],
];

function ProcessingPanel({ event }: { event: ProgressEvent | null }) {
  const progress = event?.progress;
  const done = new Set(progress?.done.map((stage) => stage.stage));
  const running = new Set(progress?.running);

  if (progress?.status === "failed") {
    return (
      <Card>
        <p className="text-sm text-rose-600" role="alert">
          Processing failed: {progress.error ?? "unknown error"}
        </p>
      </Card>
    );
  }
  return (
    <Card title="Processing">
      <ol className="flex flex-wrap gap-2" aria-live="polite">
        {STAGES.map(([stage, label]) => (
          <li
            key={stage}
            className={`rounded-full px-3 py-1 text-xs ${
              done.has(stage)
                ? "bg-emerald-100 text-emerald-800"
                : running.has(stage)
                  ? "animate-pulse bg-amber-100 text-amber-800"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800"
            }`}
          >
            {done.has(stage) ? "✓ " : ""}
            {label}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function SlideStrip({ slides, current, onSeek }: { slides: Slide[]; current: number | null; onSeek: Seek }) {
  const active = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    active.current?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [current]);

  return (
    <Card title="Slides">
      <div className="flex gap-2 overflow-x-auto pb-2">
        {slides.map((slide) => {
          const isCurrent = slide.slide_id === current;
          return (
            <button
              key={slide.slide_id}
              ref={isCurrent ? active : undefined}
              type="button"
              onClick={() => onSeek(slide.first_seen_s)}
              aria-current={isCurrent || undefined}
              title={slide.title || `Slide ${slide.slide_id + 1}`}
              className={`w-40 shrink-0 overflow-hidden rounded-lg border-2 text-left ${
                isCurrent ? "border-indigo-600" : "border-transparent opacity-70 hover:opacity-100"
              }`}
            >
              {/* Presigned storage URLs, loaded directly rather than through next/image. */}
              <img src={slide.image_url} alt={slide.title || `Slide ${slide.slide_id + 1}`} loading="lazy" className="aspect-[4/3] w-full bg-white object-contain" />
              <span className="block truncate px-1 py-0.5 text-xs">{slide.title || `Slide ${slide.slide_id + 1}`}</span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

function Chapters({ notes, time, onSeek }: { notes: StudyNotes; time: number; onSeek: Seek }) {
  const current = indexAt(notes.chapters, time);
  return (
    <Card title="Chapters">
      <ol className="flex flex-col gap-2">
        {notes.chapters.map((chapter, index) => (
          <li
            key={chapter.start_s}
            className={`rounded-lg p-2 ${index === current ? "bg-indigo-50 dark:bg-indigo-950" : ""}`}
            aria-current={index === current || undefined}
          >
            <div className="flex items-baseline gap-2">
              <TimeButton seconds={chapter.start_s} onSeek={onSeek} />
              <span className="font-medium">{chapter.title}</span>
            </div>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{chapter.summary}</p>
          </li>
        ))}
      </ol>
    </Card>
  );
}

type Tab = "transcript" | "notes" | "quiz";

function SidePanel({
  transcript,
  notes,
  time,
  onSeek,
}: {
  transcript: TranscriptLine[];
  notes: StudyNotes | undefined;
  time: number;
  onSeek: Seek;
}) {
  const [tab, setTab] = useState<Tab>("transcript");
  const tabs: [Tab, string][] = [
    ["transcript", "Transcript"],
    ["notes", "Notes"],
    ["quiz", "Quiz"],
  ];
  return (
    <section className="flex max-h-[calc(100vh-7rem)] flex-col rounded-xl border border-slate-200 bg-white shadow-sm lg:sticky lg:top-4 dark:border-slate-800 dark:bg-slate-900">
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
        {tab === "transcript" && <Transcript lines={transcript} time={time} onSeek={onSeek} />}
        {tab === "notes" && notes && <NotesPanel notes={notes} onSeek={onSeek} />}
        {tab === "quiz" && notes && <Quiz notes={notes} onSeek={onSeek} />}
      </div>
    </section>
  );
}

function Transcript({ lines, time, onSeek }: { lines: TranscriptLine[]; time: number; onSeek: Seek }) {
  const current = indexAt(lines, time);
  const [follow, setFollow] = useState(true);
  const active = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (follow) active.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [current, follow]);

  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-xs text-slate-500">
        <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
        Follow the video
      </label>
      <ol className="flex flex-col">
        {lines.map((line, index) => (
          <li key={line.index}>
            <button
              ref={index === current ? active : undefined}
              type="button"
              onClick={() => onSeek(line.start_s)}
              aria-current={index === current || undefined}
              className={`w-full rounded px-2 py-1 text-left text-sm leading-relaxed ${
                index === current ? "bg-amber-100 dark:bg-amber-900/40" : "hover:bg-slate-100 dark:hover:bg-slate-800"
              }`}
            >
              {line.text}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function NotesPanel({ notes, onSeek }: { notes: StudyNotes; onSeek: Seek }) {
  return (
    <div className="flex flex-col gap-5 text-sm">
      <section>
        <h3 className="mb-1 font-semibold">In short</h3>
        <p className="leading-relaxed">{notes.tldr}</p>
      </section>
      <section>
        <h3 className="mb-1 font-semibold">Key concepts</h3>
        <dl className="flex flex-col gap-2">
          {notes.concepts.map((concept) => (
            <div key={`${concept.term}-${concept.at_s}`}>
              <dt className="font-medium">
                {concept.term} <TimeButton seconds={concept.at_s} onSeek={onSeek} />
              </dt>
              <dd className="text-slate-600 dark:text-slate-400">{concept.definition}</dd>
            </div>
          ))}
        </dl>
      </section>
      {notes.formulas.length > 0 && (
        <section>
          <h3 className="mb-1 font-semibold">Formulas</h3>
          <ul className="flex flex-col gap-3">
            {notes.formulas.map((formula) => (
              <li key={`${formula.latex}-${formula.at_s}`}>
                <Latex source={formula.latex} />
                <p className="text-slate-600 dark:text-slate-400">
                  {formula.meaning} <TimeButton seconds={formula.at_s} onSeek={onSeek} />
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Quiz({ notes, onSeek }: { notes: StudyNotes; onSeek: Seek }) {
  return (
    <ol className="flex list-decimal flex-col gap-3 pl-5 text-sm">
      {notes.quiz.map((question) => (
        <li key={question.question}>
          <p className="font-medium">{question.question}</p>
          <details className="mt-1">
            <summary className="cursor-pointer text-indigo-700 dark:text-indigo-300">Show answer</summary>
            <p className="mt-1 text-slate-600 dark:text-slate-400">
              {question.answer} <TimeButton seconds={question.at_s} onSeek={onSeek} />
            </p>
          </details>
        </li>
      ))}
    </ol>
  );
}
