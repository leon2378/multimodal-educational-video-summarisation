"use client";

import { cn } from "cn";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, CopyIcon, PlayIcon, ScanTextIcon, SparklesIcon } from "lucide-react";
import { Fragment, memo, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Slide } from "@/lib/api";
import { formatTime } from "@/lib/timeline";

import { Latex, SectionLabel, TimeChip, scrollWithin } from "./common";

type Seek = (seconds: number) => void;

const slideTitle = (slide: Slide, index: number) => slide.title || `Slide ${index + 1}`;

/** Who read the slide: OCR alone for plain text, the vision model for figures, formulas and code. */
function ReaderBadge({ reader }: { reader: Slide["reader"] }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="relative z-10 h-5 gap-1 px-1.5 text-[0.7rem] font-normal text-muted-foreground">
          {reader === "vlm" ? <SparklesIcon /> : <ScanTextIcon />}
          {reader === "vlm" ? "Vision" : "OCR"}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-60">
        {reader === "vlm"
          ? "Read by the vision model: the slide has figures, formulas or code that OCR alone would miss."
          : "Read with OCR alone: the slide is plain text."}
      </TooltipContent>
    </Tooltip>
  );
}

/** Thumbnails under the video; the slide on screen is outlined and kept in view. */
export const Filmstrip = memo(function Filmstrip({
  slides,
  current,
  onSeek,
}: {
  slides: Slide[];
  current: number | null;
  onSeek: Seek;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const active = useRef<HTMLButtonElement>(null);
  const index = slides.findIndex((slide) => slide.slide_id === current);

  useEffect(() => {
    const box = strip.current;
    const item = active.current;
    if (!box || !item) return;
    box.scrollTo({ left: item.offsetLeft - box.clientWidth / 2 + item.clientWidth / 2, behavior: "smooth" });
  }, [current]);

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between text-sm">
        <h2 className="font-medium">Slides</h2>
        <span className="text-xs text-muted-foreground tabular-nums">
          {index >= 0 ? `${index + 1} of ${slides.length}` : `${slides.length} slides`}
        </span>
      </div>
      <div ref={strip} className="relative flex gap-2 overflow-x-auto px-0.5 pt-0.5 pb-2">
        {slides.map((slide, i) => {
          const playing = slide.slide_id === current;
          return (
            <Tooltip key={slide.slide_id}>
              <TooltipTrigger asChild>
                <button
                  ref={playing ? active : undefined}
                  type="button"
                  onClick={() => onSeek(slide.first_seen_s)}
                  aria-current={playing || undefined}
                  aria-label={`${slideTitle(slide, i)}, from ${formatTime(slide.first_seen_s)}`}
                  className={cn(
                    "relative w-28 shrink-0 overflow-hidden rounded-md bg-black ring-offset-background transition sm:w-32",
                    playing ? "ring-2 ring-primary ring-offset-2" : "opacity-70 ring-1 ring-border hover:opacity-100",
                  )}
                >
                  {/* Presigned storage URLs, loaded directly rather than through next/image. */}
                  <img src={slide.image_url} alt="" loading="lazy" className="aspect-[4/3] w-full object-cover" />
                  <span className="absolute top-1 left-1 rounded bg-black/65 px-1 text-[0.65rem] font-medium text-white tabular-nums">
                    {i + 1}
                  </span>
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {slideTitle(slide, i)} · {formatTime(slide.first_seen_s)}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </section>
  );
});

/** Every slide with what was read from it; opening one shows it in full. */
export const SlidesPanel = memo(function SlidesPanel({
  slides,
  current,
  visible,
  onSeek,
}: {
  slides: Slide[];
  current: number | null;
  visible: boolean;
  onSeek: Seek;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const active = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (visible) scrollWithin(scroller.current, active.current);
  }, [current, visible]);

  if (slides.length === 0) {
    return <p className="p-6 text-center text-sm text-muted-foreground">No slides were found in this video.</p>;
  }
  return (
    <div ref={scroller} className="h-full overflow-y-auto">
      <ol className="flex flex-col gap-1 p-3">
        {slides.map((slide, i) => {
          const playing = slide.slide_id === current;
          return (
            <li
              key={slide.slide_id}
              ref={playing ? active : undefined}
              aria-current={playing || undefined}
              className={cn(
                "relative flex gap-3 rounded-lg p-2 transition-colors",
                playing ? "bg-now" : "hover:bg-accent",
              )}
            >
              <img
                src={slide.image_url}
                alt=""
                loading="lazy"
                className="aspect-[4/3] w-24 shrink-0 rounded-md bg-black object-cover ring-1 ring-border"
              />
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                {/* The title's button covers the whole card; the time and badge sit above it. */}
                <button
                  type="button"
                  onClick={() => setOpen(i)}
                  className="line-clamp-2 text-left text-sm leading-snug font-medium after:absolute after:inset-0 after:rounded-lg"
                >
                  {slideTitle(slide, i)}
                </button>
                <div className="flex flex-wrap items-center gap-1.5">
                  <TimeChip seconds={slide.first_seen_s} onSeek={onSeek} className="relative z-10" />
                  <ReaderBadge reader={slide.reader} />
                  {slide.latex.length > 0 && (
                    <Badge variant="outline" className="h-5 px-1.5 text-[0.7rem] font-normal text-muted-foreground">
                      {slide.latex.length === 1 ? "Formula" : `${slide.latex.length} formulas`}
                    </Badge>
                  )}
                  {slide.code && (
                    <Badge variant="outline" className="h-5 px-1.5 text-[0.7rem] font-normal text-muted-foreground">
                      Code
                    </Badge>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <SlideDialog slides={slides} index={open} onIndex={setOpen} onSeek={onSeek} />
    </div>
  );
});

function SlideDialog({
  slides,
  index,
  onIndex,
  onSeek,
}: {
  slides: Slide[];
  index: number | null;
  onIndex: (index: number | null) => void;
  onSeek: Seek;
}) {
  const slide = index === null ? undefined : slides[index];
  const [copied, setCopied] = useState(false);
  const read = slide !== undefined && (slide.text_parts.length > 0 || slide.latex_not_in_text.length > 0);
  // The text sits beside the figure, unless there's only one of them, or formulas on their own
  // (a matrix, say), which need the width.
  const stacked = !read || !slide.figure_description || slide.latex_not_in_text.length > 0;
  const go = (step: number) => index !== null && onIndex(Math.min(slides.length - 1, Math.max(0, index + step)));
  const play = (seconds: number) => {
    onIndex(null);
    onSeek(seconds);
  };

  return (
    <Dialog open={slide !== undefined} onOpenChange={(open) => !open && onIndex(null)}>
      <DialogContent
        className="max-h-[90dvh] gap-5 overflow-y-auto sm:max-w-3xl"
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") go(-1);
          if (event.key === "ArrowRight") go(1);
        }}
      >
        {slide && index !== null && (
          <>
            <DialogHeader>
              <DialogTitle className="pr-6 leading-snug">{slideTitle(slide, index)}</DialogTitle>
              <DialogDescription className="flex flex-wrap items-center gap-2">
                Slide {index + 1} of {slides.length} <ReaderBadge reader={slide.reader} />
              </DialogDescription>
            </DialogHeader>
            <img
              src={slide.image_url}
              alt={slideTitle(slide, index)}
              className="max-h-[45dvh] w-full rounded-lg bg-black object-contain ring-1 ring-border"
            />
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              {/* The text with the formulas it writes out in their places, then the ones it doesn't,
                  so nothing shows twice. */}
              {read && (
                <section className={cn("min-w-0", stacked && "md:col-span-2")}>
                  <SectionLabel>{slide.text_parts.length > 0 ? "Text" : "Formulas"}</SectionLabel>
                  {slide.text_parts.length > 0 && (
                    <p className="text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
                      {slide.text_parts.map((part, i) =>
                        part.math ? <Latex key={i} source={part.value} inline /> : <Fragment key={i}>{part.value}</Fragment>,
                      )}
                    </p>
                  )}
                  {slide.latex_not_in_text.map((source) => (
                    <Latex key={source} source={source} />
                  ))}
                </section>
              )}
              {slide.figure_description && (
                <section className={cn("min-w-0", stacked && "md:col-span-2")}>
                  <SectionLabel>Figure</SectionLabel>
                  <p className="text-sm leading-relaxed text-muted-foreground">{slide.figure_description}</p>
                </section>
              )}
              {slide.code && (
                <section className="md:col-span-2">
                  <SectionLabel>Code</SectionLabel>
                  <div className="relative">
                    <pre className="overflow-x-auto rounded-lg border bg-muted/50 p-3 pr-10 font-mono text-xs leading-relaxed">
                      {slide.code}
                    </pre>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="absolute top-2 right-2"
                      aria-label="Copy the code"
                      onClick={() => {
                        void navigator.clipboard.writeText(slide.code);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      }}
                    >
                      {copied ? <CheckIcon /> : <CopyIcon />}
                    </Button>
                  </div>
                </section>
              )}
              <section className="md:col-span-2">
                <SectionLabel>On screen</SectionLabel>
                <div className="flex flex-wrap gap-1.5">
                  {slide.spans.map((span) => (
                    <TimeChip
                      key={span.start_s}
                      seconds={span.start_s}
                      label={`${formatTime(span.start_s)}–${formatTime(span.end_s)}`}
                      onSeek={play}
                    />
                  ))}
                </div>
              </section>
            </div>
            <div className="flex items-center gap-2 border-t pt-4">
              <Button variant="outline" size="icon-sm" onClick={() => go(-1)} disabled={index === 0} aria-label="Previous slide">
                <ChevronLeftIcon />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => go(1)}
                disabled={index === slides.length - 1}
                aria-label="Next slide"
              >
                <ChevronRightIcon />
              </Button>
              <span className="text-xs text-muted-foreground">Arrow keys move between slides</span>
              <Button className="ml-auto" onClick={() => play(slide.first_seen_s)}>
                <PlayIcon /> Play from {formatTime(slide.first_seen_s)}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
