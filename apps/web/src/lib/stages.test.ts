import { describe, expect, it } from "vitest";

import { PHASES, phasesFor, stageLabel, summarise } from "./stages";

const total = PHASES.flatMap((phase) => phase.stages).length;

describe("summarise", () => {
  it("counts done stages and tells running from pending", () => {
    const run = summarise({
      status: "running",
      running: ["asr", "ocr"],
      done: [
        { stage: "probe", seconds: 0.3, cached: false },
        { stage: "audio", seconds: 4.1, cached: true },
        { stage: "slides", seconds: 20, cached: false },
      ],
      error: null,
    });
    expect(run.done).toBe(3);
    expect(run.total).toBe(total);
    expect(run.percent).toBe(Math.round((3 / total) * 100));
    expect(run.state("audio")).toBe("done");
    expect(run.state("asr")).toBe("running");
    expect(run.state("notes")).toBe("pending");
    expect(run.info("audio")?.cached).toBe(true);
  });

  it("calls a succeeded run complete, save included", () => {
    const run = summarise({ status: "succeeded", running: [], done: [], error: null });
    expect(run.percent).toBe(100);
    expect(run.state("save")).toBe("done");
  });

  it("starts at nothing without progress", () => {
    const run = summarise(null);
    expect(run.done).toBe(0);
    expect(run.state("probe")).toBe("pending");
  });

  it("ignores stages it doesn't know", () => {
    const run = summarise({
      status: "running",
      running: [],
      done: [{ stage: "detector", seconds: 1, cached: false }],
      error: null,
    });
    expect(run.done).toBe(0);
  });

  it("counts the download only for a lecture from a link", () => {
    const progress = {
      status: "running" as const,
      running: ["probe"],
      done: [{ stage: "fetch", seconds: 30, cached: false }],
      error: null,
    };
    expect(summarise(progress).done).toBe(0);
    const run = summarise(progress, true);
    expect(run.done).toBe(1);
    expect(run.total).toBe(total + 1);
    expect(run.state("fetch")).toBe("done");
  });
});

describe("phasesFor", () => {
  it("puts the download first for a link, and leaves uploads alone", () => {
    expect(phasesFor(false)).toBe(PHASES);
    expect(phasesFor(true)[0]?.stages[0]?.id).toBe("fetch");
    expect(phasesFor(true).flatMap((phase) => phase.stages)).toHaveLength(total + 1);
    expect(PHASES[0]?.stages[0]?.id).toBe("probe");
  });
});

describe("stageLabel", () => {
  it("names known stages and tidies unknown ones", () => {
    expect(stageLabel("draft_notes")).toBe("Writing notes");
    expect(stageLabel("fetch")).toBe("Downloading the video");
    expect(stageLabel("new_stage")).toBe("new stage");
  });
});
