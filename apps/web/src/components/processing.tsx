"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  ChevronDownIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  ClockIcon,
  CloudUploadIcon,
  PlayIcon,
  RotateCcwIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { refusal } from "@/lib/access";
import { type Lecture, type ProgressEvent, api, unwrap } from "@/lib/api";
import { formatCost, formatDuration, formatRelative, formatSeconds } from "@/lib/format";
import { lectureKey, meKey, useRuns } from "@/lib/queries";
import { phasesFor, stageLabel, summarise } from "@/lib/stages";

import { useAccount } from "./account";
import { Callout } from "./common";
import { useUpload } from "./upload";

/** Starts processing (again). The API is idempotent: while a run is going it returns that run. */
export function useStartProcessing(id: string) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      unwrap(await api.POST("/v1/lectures/{lecture_id}/process", { params: { path: { lecture_id: id } } }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: lectureKey(id) }),
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
        queryClient.invalidateQueries({ queryKey: meKey }),
      ]);
    } catch (e) {
      toast.error("Couldn't start processing", { description: refusal(e) });
    } finally {
      setBusy(false);
    }
  };
  return { start, busy };
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/** Where the lecture's processing is, in place of the study panel until it's ready. */
export function ProcessingPanel({ lecture, event }: { lecture: Lecture; event: ProgressEvent | null }) {
  const { start, busy } = useStartProcessing(lecture.id);
  const { openUpload } = useUpload();
  // Processing is for the lecture's owner (or an admin); others see where it stands.
  const mine = useAccount().canChange(lecture);
  const status = lecture.status;
  const running = status === "processing";
  const runs = useRuns(lecture.id, running || status === "failed");
  const now = useNow(running);
  const progress = event?.progress;
  // A lecture from a link downloads its video first.
  const fromLink = lecture.source_url !== null;
  const run = summarise(progress, fromLink);
  const latest = runs.data?.[0];

  if (status === "awaiting_upload" || status === "uploaded") {
    const waiting = status === "awaiting_upload";
    return (
      <Shell>
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
            {waiting ? <CloudUploadIcon className="size-5" /> : <ClockIcon className="size-5" />}
          </span>
          <div className="flex flex-col gap-1">
            <p className="font-semibold">{waiting ? "The upload didn't finish" : "Not processed yet"}</p>
            <p className="max-w-xs text-sm text-muted-foreground">
              {waiting
                ? "Not all of the video reached storage. Choose the same file again to continue: what was uploaded is kept."
                : mine
                  ? "Process the lecture to get its transcript, slides, notes, quiz, search and Q&A."
                  : "Its transcript, notes and the rest appear once its owner processes it."}
            </p>
          </div>
          {mine &&
            (waiting ? (
              <Button onClick={() => openUpload({ resume: lecture })}>
                <CloudUploadIcon /> Continue the upload
              </Button>
            ) : (
              <Button onClick={() => void start()} disabled={busy}>
                {busy ? <Spinner /> : <PlayIcon />} Process
              </Button>
            ))}
        </div>
      </Shell>
    );
  }

  const failed = status === "failed";
  const elapsed = latest && running ? (now - new Date(latest.started_at).getTime()) / 1000 : null;
  return (
    <Shell>
      <div className="border-b p-5">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex size-10 shrink-0 items-center justify-center rounded-full",
              failed ? "bg-destructive/10 text-destructive" : "bg-brand-soft text-brand-ink",
            )}
          >
            {failed ? <CircleAlertIcon className="size-5" /> : <Spinner className="size-5" />}
          </span>
          <div className="min-w-0">
            <p className="font-semibold">{failed ? "Processing failed" : "Processing this lecture"}</p>
            <p className="text-sm text-muted-foreground tabular-nums">
              {failed
                ? `Stopped after ${run.done} of ${run.total} steps`
                : `Step ${Math.min(run.done + 1, run.total)} of ${run.total}${elapsed !== null ? ` · ${formatDuration(elapsed)} so far` : ""}`}
            </p>
          </div>
          {failed && mine && (
            <Button className="ml-auto" size="sm" onClick={() => void start()} disabled={busy}>
              {busy ? <Spinner /> : <RotateCcwIcon />} Try again
            </Button>
          )}
        </div>
        {!failed && <Progress value={Math.max(run.percent, 3)} className="mt-4 h-1.5" />}
        {failed && (
          <Callout tone="error" className="mt-4">
            {progress?.error ?? latest?.error ?? "The run stopped without saying why."}
          </Callout>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <ol className="flex flex-col gap-5" aria-live="polite">
          {phasesFor(fromLink).map((phase) => (
            <li key={phase.label}>
              <p className="mb-1.5 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                {phase.label}
              </p>
              <ul className="flex flex-col">
                {phase.stages.map((stage) => {
                  const state = run.state(stage.id);
                  const info = run.info(stage.id);
                  return (
                    <li key={stage.id} className="flex items-center gap-3 py-1.5 text-sm">
                      {state === "done" ? (
                        <CircleCheckIcon className="size-4 shrink-0 text-success" />
                      ) : state === "running" && !failed ? (
                        <Spinner className="size-4 shrink-0 text-primary" />
                      ) : (
                        <CircleDashedIcon className="size-4 shrink-0 text-muted-foreground/50" />
                      )}
                      <span className={cn(state === "pending" && "text-muted-foreground", state === "running" && "font-medium")}>
                        {stage.label}
                      </span>
                      <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
                        {info?.cached && (
                          <Badge variant="outline" className="h-5 px-1.5 text-[0.65rem] font-normal">
                            cached
                          </Badge>
                        )}
                        {info && !info.cached && formatSeconds(info.seconds)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ol>
      </div>
      {!failed && (
        <p className="border-t px-5 py-3 text-xs text-muted-foreground">
          You can leave this page: processing carries on. The notes, transcript, quiz and Q&amp;A appear here when
          it&apos;s done.
        </p>
      )}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm">{children}</div>;
}

interface Usage {
  model?: string;
  requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number | null;
}

/** Every processing run of a lecture: its stages' times, what was cached, and the LLM usage. */
export function RunsDialog({
  lectureId,
  open,
  onOpenChange,
}: {
  lectureId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const runs = useRuns(lectureId, open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Processing history</DialogTitle>
          <DialogDescription>
            Each run&apos;s stages and how long they took. Cached stages reused an earlier run&apos;s output, since
            their inputs hadn&apos;t changed.
          </DialogDescription>
        </DialogHeader>
        {runs.isPending && <Skeleton className="h-40" />}
        {runs.isError && <Callout tone="error">{runs.error.message}</Callout>}
        <div className="flex flex-col gap-2">
          {runs.data?.map((run, index) => {
            const usage = run.llm_usage as Usage | null;
            const seconds = run.finished_at
              ? (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
              : null;
            // Cached stages only looked their output up, so they don't set the scale.
            const slowest = Math.max(...run.stages.filter((s) => !s.cached).map((s) => s.seconds), 0.001);
            return (
              <Collapsible key={run.id} defaultOpen={index === 0} className="rounded-lg border">
                <CollapsibleTrigger className="group flex w-full items-center gap-3 px-4 py-3 text-left text-sm">
                  <Badge
                    className={cn(
                      run.status === "succeeded" && "bg-success/15 text-success",
                      run.status === "failed" && "bg-destructive/15 text-destructive",
                      run.status === "running" && "bg-warning/15 text-warning",
                    )}
                  >
                    {run.status === "succeeded" ? "Succeeded" : run.status === "failed" ? "Failed" : "Running"}
                  </Badge>
                  <span className="font-medium">{formatRelative(run.started_at)}</span>
                  <span className="text-muted-foreground tabular-nums">
                    {seconds !== null && formatSeconds(seconds)}
                    {usage?.cost_usd != null && ` · ${formatCost(usage.cost_usd)}`}
                  </span>
                  <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-data-[state=closed]:-rotate-90" />
                </CollapsibleTrigger>
                <CollapsibleContent className="border-t px-4 py-3">
                  {run.error && (
                    <Callout tone="error" className="mb-3">
                      {run.error}
                    </Callout>
                  )}
                  {usage?.model && (
                    <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
                      {[
                        ["Model", usage.model.replace(/^[a-z-]+:/, "")],
                        ["LLM calls", usage.requests],
                        ["Tokens in / out", `${(usage.input_tokens ?? 0).toLocaleString()} / ${(usage.output_tokens ?? 0).toLocaleString()}`],
                        ["Cost at paid rates", usage.cost_usd != null ? formatCost(usage.cost_usd) : "unknown"],
                      ].map(([term, value]) => (
                        <div key={String(term)}>
                          <dt className="text-muted-foreground">{term}</dt>
                          <dd className="truncate font-medium tabular-nums">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {run.stages.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No stages finished.</p>
                  ) : (
                    <ul className="flex flex-col gap-1.5">
                      {run.stages.map((stage) => (
                        <li key={stage.stage} className="grid grid-cols-[10rem_1fr_4.5rem] items-center gap-3 text-xs">
                          <span className="truncate">{stageLabel(stage.stage)}</span>
                          <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                            {!stage.cached && (
                              <span
                                className="block h-full rounded-full bg-primary"
                                style={{ width: `${Math.max((stage.seconds / slowest) * 100, 2)}%` }}
                              />
                            )}
                          </span>
                          <span className="text-right text-muted-foreground tabular-nums">
                            {stage.cached ? "cached" : formatSeconds(stage.seconds)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CollapsibleContent>
              </Collapsible>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
