import { describe, expect, it } from "vitest";

import { formatTime, indexAt, slideAt } from "./timeline";

describe("formatTime", () => {
  it.each([
    [0, "00:00"],
    [59.9, "00:59"],
    [727, "12:07"],
    [3600, "1:00:00"],
    [3725, "1:02:05"],
    [-3, "00:00"],
  ])("formats %s as %s", (seconds, text) => {
    expect(formatTime(seconds)).toBe(text);
  });
});

describe("indexAt", () => {
  const lines = [{ start_s: 0.2 }, { start_s: 2.2 }, { start_s: 6.2 }, { start_s: 9.2 }];

  it("finds the line playing now", () => {
    expect(indexAt(lines, 2.2)).toBe(1);
    expect(indexAt(lines, 5.9)).toBe(1);
    expect(indexAt(lines, 100)).toBe(3);
  });

  it("returns -1 before the first line and for no lines", () => {
    expect(indexAt(lines, 0.1)).toBe(-1);
    expect(indexAt([], 5)).toBe(-1);
  });
});

describe("slideAt", () => {
  const slides = [
    {
      slide_id: 0,
      spans: [
        { start_s: 2, end_s: 6 },
        { start_s: 9, end_s: 12 },
      ],
    },
    { slide_id: 1, spans: [{ start_s: 6, end_s: 9 }] },
  ];

  it("follows the slide on screen, including a slide shown again", () => {
    expect(slideAt(slides, 3)).toBe(0);
    expect(slideAt(slides, 6)).toBe(1);
    expect(slideAt(slides, 10)).toBe(0);
  });

  it("has no slide before the first one appears", () => {
    expect(slideAt(slides, 1)).toBeNull();
  });
});
