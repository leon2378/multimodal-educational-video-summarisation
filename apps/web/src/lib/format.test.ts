import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatCost,
  formatDuration,
  formatRelative,
  formatSeconds,
  hueFor,
  pluralise,
  titleFromFilename,
} from "./format";

describe("formatDuration", () => {
  it("reads as seconds, minutes, then hours and minutes", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(79.6)).toBe("1 min");
    expect(formatDuration(3085.7)).toBe("51 min");
    expect(formatDuration(3780)).toBe("1 h 3 min");
    expect(formatDuration(7200)).toBe("2 h");
  });
});

describe("formatSeconds", () => {
  it("keeps a decimal for short stages only", () => {
    expect(formatSeconds(0.42)).toBe("0.4 s");
    expect(formatSeconds(12.4)).toBe("12 s");
    expect(formatSeconds(125)).toBe("2 min 5 s");
    expect(formatSeconds(120)).toBe("2 min");
  });
});

describe("formatBytes", () => {
  it("uses decimal units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1_234_567)).toBe("1.2 MB");
    expect(formatBytes(254_000_000)).toBe("254 MB");
    expect(formatBytes(2_100_000_000)).toBe("2.1 GB");
  });
});

describe("formatCost", () => {
  it("doesn't round small costs to zero", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(0.00003)).toBe("<$0.0001");
    expect(formatCost(0.00031)).toBe("$0.00031");
    expect(formatCost(0.21)).toBe("$0.21");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-09-29T12:00:00Z");
  it("counts back from now", () => {
    expect(formatRelative("2026-09-29T11:59:30Z", now)).toBe("just now");
    expect(formatRelative("2026-09-29T11:15:00Z", now)).toBe("45 min ago");
    expect(formatRelative("2026-09-29T02:00:00Z", now)).toBe("10 h ago");
    expect(formatRelative("2026-09-28T10:00:00Z", now)).toBe("yesterday");
    expect(formatRelative("2026-09-25T12:00:00Z", now)).toBe("4 days ago");
  });
  it("gives the date after a week", () => {
    expect(formatRelative("2026-09-01T12:00:00Z", now)).toBe("1 Sept");
    expect(formatRelative("2025-12-24T12:00:00Z", now)).toBe("24 Dec 2025");
  });
});

describe("titleFromFilename", () => {
  it("drops the extension and separators", () => {
    expect(titleFromFilename("lecture_03-sorting.mp4")).toBe("Lecture 03 sorting");
    expect(titleFromFilename("MIT6_0001F16_Lec10.mp4")).toBe("MIT6 0001F16 Lec10");
    expect(titleFromFilename("6.006 intro.webm")).toBe("6.006 intro");
  });
});

describe("hueFor", () => {
  it("is stable and in range", () => {
    const hue = hueFor("13759f9e-6141-4c9b-9907-cda5e708eb05");
    expect(hue).toBe(hueFor("13759f9e-6141-4c9b-9907-cda5e708eb05"));
    expect(hue).toBeGreaterThanOrEqual(0);
    expect(hue).toBeLessThan(360);
  });
});

describe("pluralise", () => {
  it("adds an s unless told otherwise", () => {
    expect(pluralise(1, "lecture")).toBe("1 lecture");
    expect(pluralise(3, "lecture")).toBe("3 lectures");
    expect(pluralise(2, "passage")).toBe("2 passages");
  });
});
