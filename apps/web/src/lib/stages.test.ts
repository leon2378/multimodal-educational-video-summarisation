import { describe, expect, it } from "vitest";

import { PHASES, stageLabel, summarise } from "./stages";

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
});

describe("stageLabel", () => {
  it("names known stages and tidies unknown ones", () => {
    expect(stageLabel("draft_notes")).toBe("Writing notes");
    expect(stageLabel("new_stage")).toBe("new stage");
  });
});
