import { describe, expect, it } from "vitest";

import { lectureHref, startTime } from "./scope";

describe("lectureHref", () => {
  it("opens a lecture, optionally at a whole second", () => {
    expect(lectureHref("abc")).toBe("/lectures/abc");
    expect(lectureHref("abc", 754.9)).toBe("/lectures/abc?t=754");
  });
});

describe("startTime", () => {
  it("reads ?t= and ignores anything that isn't a time", () => {
    expect(startTime("754")).toBe(754);
    expect(startTime(["12", "99"])).toBe(12);
    expect(startTime(undefined)).toBeUndefined();
    expect(startTime("soon")).toBeUndefined();
    expect(startTime("-5")).toBeUndefined();
  });
});
