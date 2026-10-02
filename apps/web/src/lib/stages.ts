/** The processing pipeline's stages, grouped the way the workflow runs them
 *  (lecture_pipeline.temporal.workflow), and how far a run has got. */

import type { components } from "./api/schema";

type Progress = components["schemas"]["Progress"];
type StageInfo = components["schemas"]["StageInfo"];

export interface Phase {
  label: string;
  stages: { id: string; label: string }[];
}

/** A lecture added from a link downloads its video first. */
const FETCH = { id: "fetch", label: "Downloading the video" };

/** Speech and slides run side by side, then notes and search embeddings. */
export const PHASES: Phase[] = [
  {
    label: "Ingest",
    stages: [
      { id: "probe", label: "Reading the video" },
      { id: "audio", label: "Extracting audio" },
    ],
  },
  { label: "Speech", stages: [{ id: "asr", label: "Transcribing speech" }] },
  {
    label: "Slides",
    stages: [
      { id: "slides", label: "Finding slides" },
      { id: "ocr", label: "Reading slide text" },
      { id: "read_slides", label: "Reading figures and formulas" },
    ],
  },
  {
    label: "Notes",
    stages: [
      { id: "timeline", label: "Aligning speech with slides" },
      { id: "chapters", label: "Planning chapters" },
      { id: "draft_notes", label: "Writing notes" },
      { id: "notes", label: "Assembling notes" },
    ],
  },
  {
    label: "Search",
    stages: [
      { id: "embed", label: "Embedding passages" },
      { id: "index", label: "Indexing for search" },
    ],
  },
  { label: "Finish", stages: [{ id: "save", label: "Saving results" }] },
];

/** The stages a lecture's run goes through: an upload's, or a link's with the download first. */
export function phasesFor(fromLink: boolean): Phase[] {
  if (!fromLink) return PHASES;
  return PHASES.map((phase, index) => (index === 0 ? { ...phase, stages: [FETCH, ...phase.stages] } : phase));
}

export type StageState = "done" | "running" | "pending";

export interface RunSummary {
  done: number;
  total: number;
  /** 0 to 100. A finished run is 100 even though "save" is never reported done. */
  percent: number;
  state: (stage: string) => StageState;
  info: (stage: string) => StageInfo | undefined;
}

export function summarise(progress: Progress | null | undefined, fromLink = false): RunSummary {
  const ids = phasesFor(fromLink).flatMap((phase) => phase.stages.map((stage) => stage.id));
  const done = new Map((progress?.done ?? []).map((info) => [info.stage, info]));
  const running = new Set(progress?.running ?? []);
  const count = ids.filter((stage) => done.has(stage)).length;
  const finished = progress?.status === "succeeded";
  return {
    done: finished ? ids.length : count,
    total: ids.length,
    percent: finished ? 100 : Math.round((count / ids.length) * 100),
    state: (stage) =>
      done.has(stage) || finished ? "done" : running.has(stage) ? "running" : "pending",
    info: (stage) => done.get(stage),
  };
}

/** Every stage's name in a label, for the run history ("draft_notes" -> "Writing notes"). */
export function stageLabel(stage: string): string {
  for (const phase of phasesFor(true)) {
    const found = phase.stages.find((s) => s.id === stage);
    if (found) return found.label;
  }
  return stage.replaceAll("_", " ");
}
