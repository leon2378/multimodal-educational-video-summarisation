"use client";

import { cn } from "cn";
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
import { type ReactNode, Suspense, use, useCallback, useEffect, useMemo, useState } from "react";

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

type Katex = typeof import("katex").default;
let katexModule: Promise<Katex | null> | undefined;

/** KaTeX is a large script that only formulas need, so it loads on first use rather than with
 *  every page. Pages that are likely to show formulas call this early to have it ready. */
export function loadKatex(): Promise<Katex | null> {
  katexModule ??= import("katex").then(
    (module) => module.default,
    // Couldn't load (offline, say): formulas stay as their source.
    () => null,
  );
  return katexModule;
}

/** LaTeX from the notes or an answer, rendered by KaTeX. It comes from an LLM, so it's untrusted:
 *  KaTeX escapes its input, and with `trust` off it refuses \href, \url and raw HTML. The
 *  source shows until KaTeX has loaded. */
export function Latex({ source, inline = false }: { source: string; inline?: boolean }) {
  const plain = inline ? (
    <span className="font-mono text-[0.9em]">{source}</span>
  ) : (
    <div className="overflow-x-auto py-1 font-mono text-sm text-muted-foreground">{source}</div>
  );
  return (
    <Suspense fallback={plain}>
      <Rendered source={source} inline={inline} plain={plain} />
    </Suspense>
  );
}

function Rendered({ source, inline, plain }: { source: string; inline: boolean; plain: ReactNode }) {
  const katex = use(loadKatex());
  const html = useMemo(
    () => katex?.renderToString(source, { throwOnError: false, displayMode: !inline, trust: false }),
    [katex, source, inline],
  );
  if (html === undefined) return plain;
  return inline ? (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <div className="overflow-x-auto py-1" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

/** A ref for an element, and whether it has come near the viewport. It stays true once it has,
 *  so whatever it starts loading is kept. */
export function useSeen<T extends Element>(margin = "300px"): [(node: T | null) => void, boolean] {
  const [node, setNode] = useState<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || !node) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
    }, { rootMargin: margin });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, seen, margin]);
  return [setNode, seen];
}

/** A lecture's cover: its first slide once it's processed, over a colour of its own that shows
 *  until the slide has loaded. Its slides are asked for only once the cover is near the screen,
 *  so a long library doesn't load every lecture's slides at once. */
export function LectureCover({
  lecture,
  className,
  children,
}: {
  lecture: Lecture;
  className?: string;
  children?: ReactNode;
}) {
  const [ref, seen] = useSeen<HTMLDivElement>();
  const slides = useSlides(lecture.id, lecture.status === "ready" && seen);
  const first = slides.data?.[0];
  // The slide frame's shape once it has loaded, and a link that failed to load (it may have
  // expired; the slides' next refresh brings a new one).
  const [shape, setShape] = useState<"wide" | "narrow" | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const hue = hueFor(lecture.id);
  const measure = (image: HTMLImageElement) =>
    setShape(image.naturalWidth / image.naturalHeight < 1.5 ? "narrow" : "wide");
  return (
    <div
      ref={ref}
      className={cn("relative isolate overflow-hidden bg-muted", className)}
      style={{
        backgroundImage: `linear-gradient(135deg, oklch(0.72 0.12 ${hue}), oklch(0.46 0.15 ${(hue + 50) % 360}))`,
      }}
    >
      <div className="absolute inset-0 flex items-center justify-center">
        <FilmIcon className="size-8 text-white/80" aria-hidden />
      </div>
      {first && first.image_url !== failed && (
        // Presigned storage URLs, loaded directly rather than through next/image. A 4:3 frame
        // often has dark bars at its sides, and a thin one at the top: zoom past them, keeping
        // the top, where a slide's title is. A 16:9 frame fills the cover as it is.
        <img
          ref={(image) => {
            if (image?.complete && image.naturalWidth > 0) measure(image);
          }}
          src={first.image_url}
          alt=""
          decoding="async"
          onLoad={(event) => measure(event.currentTarget)}
          onError={() => setFailed(first.image_url)}
          className={cn(
            "absolute inset-0 size-full object-cover transition-opacity duration-300",
            shape ? "opacity-100" : "opacity-0",
            shape === "narrow" && "origin-top -translate-y-[2%] scale-[1.34] object-top",
          )}
        />
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
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  container.scrollTo({ top, behavior: smooth && !still ? "smooth" : "auto" });
}
