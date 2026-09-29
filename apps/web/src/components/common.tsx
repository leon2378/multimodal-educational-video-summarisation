"use client";

import { cn } from "cn";
import katex from "katex";
import {
  CircleAlertIcon,
  CircleCheckIcon,
  ClockIcon,
  FilmIcon,
  InfoIcon,
  LoaderCircleIcon,
  PlayIcon,
  UploadIcon,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { Lecture, LectureStatus } from "@/lib/api";
import { hueFor } from "@/lib/format";
import { useSlides } from "@/lib/queries";
import { formatTime } from "@/lib/timeline";

const STATUS: Record<LectureStatus, { label: string; icon: ReactNode; className: string }> = {
  awaiting_upload: {
    label: "Awaiting upload",
    icon: <UploadIcon />,
    className: "bg-muted text-muted-foreground",
  },
  uploaded: { label: "Not processed", icon: <ClockIcon />, className: "bg-muted text-muted-foreground" },
  processing: {
    label: "Processing",
    icon: <LoaderCircleIcon className="animate-spin" />,
    className: "bg-warning/15 text-warning",
  },
  ready: { label: "Ready", icon: <CircleCheckIcon />, className: "bg-success/15 text-success" },
  failed: { label: "Failed", icon: <CircleAlertIcon />, className: "bg-destructive/15 text-destructive" },
};

export function StatusBadge({ status, className }: { status: LectureStatus; className?: string }) {
  const { label, icon, className: tone } = STATUS[status];
  return (
    <Badge className={cn(tone, className)}>
      {icon}
      {label}
    </Badge>
  );
}

/** A timestamp that plays the video from there, like [12:30]. `label` replaces the text shown,
 *  e.g. "L2 12:30", and `lecture` names the lecture it plays. */
export function TimeChip({
  seconds,
  onSeek,
  label,
  lecture,
  className,
}: {
  seconds: number;
  onSeek: (t: number) => void;
  label?: string;
  lecture?: string | null;
  className?: string;
}) {
  const where = lecture ? `${lecture} from ${formatTime(seconds)}` : `from ${formatTime(seconds)}`;
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onSeek(seconds);
      }}
      aria-label={`Play ${where}`}
      title={lecture ?? undefined}
      className={cn(
        "inline-flex items-center gap-1 rounded-md bg-brand-soft px-1.5 py-px align-baseline font-mono text-[0.8em] font-medium text-brand-ink tabular-nums transition-colors hover:bg-primary hover:text-primary-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        className,
      )}
    >
      <PlayIcon className="size-[0.8em] fill-current" aria-hidden />
      {(label ?? formatTime(seconds)).replace(/^\[|\]$/g, "")}
    </button>
  );
}

/** LaTeX from the notes or an answer, rendered by KaTeX. It comes from an LLM, so it's untrusted:
 *  KaTeX escapes its input, and with `trust` off it refuses \href, \url and raw HTML. */
export function Latex({ source, inline = false }: { source: string; inline?: boolean }) {
  const html = useMemo(
    () => katex.renderToString(source, { throwOnError: false, displayMode: !inline, trust: false }),
    [source, inline],
  );
  return inline ? (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <div className="overflow-x-auto py-1" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

/** A lecture's cover: its first slide once it's processed, otherwise a colour of its own. */
export function LectureCover({
  lecture,
  className,
  children,
}: {
  lecture: Lecture;
  className?: string;
  children?: ReactNode;
}) {
  const ready = lecture.status === "ready";
  const slides = useSlides(lecture.id, ready);
  const first = slides.data?.[0];
  const hue = hueFor(lecture.id);
  return (
    <div
      className={cn("relative isolate overflow-hidden bg-muted", className)}
      style={
        first
          ? undefined
          : {
              backgroundImage: `linear-gradient(135deg, oklch(0.72 0.12 ${hue}), oklch(0.46 0.15 ${(hue + 50) % 360}))`,
            }
      }
    >
      {first ? (
        // Presigned storage URLs, loaded directly rather than through next/image.
        // Slide frames are 4:3 with dark bars at the sides; zoom past them to fill a 16:9 cover.
        <img src={first.image_url} alt="" loading="lazy" className="size-full scale-[1.22] bg-white object-cover" />
      ) : (
        <div className="flex size-full items-center justify-center">
          <FilmIcon className="size-8 text-white/80" aria-hidden />
        </div>
      )}
      {lecture.status === "processing" && (
        <div className="absolute inset-0 animate-pulse bg-gradient-to-r from-transparent via-white/15 to-transparent" />
      )}
      {children}
    </div>
  );
}

const TONES = {
  info: { icon: InfoIcon, className: "border-border bg-muted/50 text-foreground" },
  warning: { icon: CircleAlertIcon, className: "border-warning/30 bg-warning/10 text-foreground" },
  error: { icon: CircleAlertIcon, className: "border-destructive/30 bg-destructive/10 text-foreground" },
};

/** A boxed message: something to know, or something that went wrong. */
export function Callout({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: keyof typeof TONES;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const { icon: Icon, className: toneClass } = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn("flex gap-3 rounded-lg border px-4 py-3 text-sm", toneClass, className)}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "error" ? "text-destructive" : tone === "warning" ? "text-warning" : "text-muted-foreground",
        )}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="text-muted-foreground">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

/** A small heading over a section of a panel, with an optional count. */
export function SectionLabel({ children, count }: { children: ReactNode; count?: number }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
      {children}
      {count !== undefined && (
        <span className="rounded-full bg-muted px-1.5 py-px text-[0.7rem] font-medium tabular-nums">{count}</span>
      )}
    </h3>
  );
}

/** The app's mark: a play button becoming lines of notes. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7", className)}>
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <path d="M9 9.5v8l6.5-4z" fill="white" />
      <rect x="18" y="10.5" width="6" height="2" rx="1" fill="white" fillOpacity=".9" />
      <rect x="9" y="20" width="15" height="2" rx="1" fill="white" fillOpacity=".75" />
      <rect x="9" y="24" width="10" height="2" rx="1" fill="white" fillOpacity=".55" />
    </svg>
  );
}

/** State kept in localStorage under `key`, read after mounting so the server's HTML matches the
 *  first render. It still works, unsaved, where storage is blocked. */
export function useStoredState<T>(key: string, initial: T): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    try {
      const stored = localStorage.getItem(key);
      if (stored !== null) setValue(JSON.parse(stored) as T);
    } catch {
      // Blocked or unreadable: start from `initial`.
    }
  }, [key]);
  const store = useCallback(
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // Kept for this visit only.
      }
    },
    [key],
  );
  return [value, store];
}

export function useDocumentTitle(title: string | undefined) {
  useEffect(() => {
    if (title) document.title = `${title} · Lecture Summariser`;
  }, [title]);
}

/** Scrolls `element` into view inside `container` only, so a following list never moves the
 *  page itself (scrollIntoView would, on a phone where the panel sits below the video). */
export function scrollWithin(container: HTMLElement | null, element: HTMLElement | null, smooth = true) {
  if (!container || !element) return;
  const box = container.getBoundingClientRect();
  const item = element.getBoundingClientRect();
  if (item.top >= box.top + 24 && item.bottom <= box.bottom - 24) return;
  const top = container.scrollTop + (item.top - box.top) - box.height / 3;
  container.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
}
