import { describe, expect, it } from "vitest";

import type { Citation, Source } from "./api";
import { citationSeconds, parseAnswer, parseInline, resolveCitation } from "./answer";

describe("citationSeconds", () => {
  it("reads mm:ss and h:mm:ss", () => {
    expect(citationSeconds("[01:30]")).toBe(90);
    expect(citationSeconds("[75:10]")).toBe(4510);
    expect(citationSeconds("[1:02:03]")).toBe(3723);
  });

  it("rejects impossible times", () => {
    expect(citationSeconds("[12:60]")).toBeNull();
    expect(citationSeconds("[1:60:00]")).toBeNull();
  });
});

describe("parseInline", () => {
  it("finds citations, bold, code and maths", () => {
    expect(parseInline("It is **linear** [01:30], see `fact(n)` and $O(n^2)$.")).toEqual([
      { kind: "text", text: "It is " },
      { kind: "bold", children: [{ kind: "text", text: "linear" }] },
      { kind: "text", text: " " },
      { kind: "cite", label: "[01:30]", seconds: 90, lecture: null },
      { kind: "text", text: ", see " },
      { kind: "code", text: "fact(n)" },
      { kind: "text", text: " and " },
      { kind: "math", tex: "O(n^2)" },
      { kind: "text", text: "." },
    ]);
  });

  it("splits several times in one bracket", () => {
    expect(parseInline("see [28:43, 28:59]")).toEqual([
      { kind: "text", text: "see " },
      { kind: "cite", label: "[28:43]", seconds: 1723, lecture: null },
      { kind: "cite", label: "[28:59]", seconds: 1739, lecture: null },
    ]);
  });

  it("reads lecture labels across a course", () => {
    expect(parseInline("[L2 01:30] and [L1 00:05, 00:09; L3 1:00:00]")).toEqual([
      { kind: "cite", label: "[L2 01:30]", seconds: 90, lecture: "L2" },
      { kind: "text", text: " and " },
      { kind: "cite", label: "[L1 00:05]", seconds: 5, lecture: "L1" },
      { kind: "cite", label: "[L1 00:09]", seconds: 9, lecture: "L1" },
      { kind: "cite", label: "[L3 1:00:00]", seconds: 3600, lecture: "L3" },
    ]);
  });

  it("shows a repeated citation once", () => {
    expect(parseInline("loops [34:54] [34:54]. Then [34:54, 34:54]")).toEqual([
      { kind: "text", text: "loops " },
      { kind: "cite", label: "[34:54]", seconds: 2094, lecture: null },
      { kind: "text", text: ". Then " },
      { kind: "cite", label: "[34:54]", seconds: 2094, lecture: null },
    ]);
  });

  it("cites inside bold", () => {
    expect(parseInline("**Quadratic [12:00]**")).toEqual([
      {
        kind: "bold",
        children: [
          { kind: "text", text: "Quadratic " },
          { kind: "cite", label: "[12:00]", seconds: 720, lecture: null },
        ],
      },
    ]);
  });

  it("leaves unfinished markup as text while the answer streams", () => {
    expect(parseInline("It is **lin")).toEqual([{ kind: "text", text: "It is **lin" }]);
    expect(parseInline("costs $O(n")).toEqual([{ kind: "text", text: "costs $O(n" }]);
    expect(parseInline("at [12:3")).toEqual([{ kind: "text", text: "at [12:3" }]);
  });

  it("keeps HTML as text", () => {
    expect(parseInline("<img src=x onerror=alert(1)>")).toEqual([
      { kind: "text", text: "<img src=x onerror=alert(1)>" },
    ]);
  });
});

describe("parseAnswer", () => {
  it("splits paragraphs and lists", () => {
    const blocks = parseAnswer(
      "Two laws [33:22]:\n\n- Addition: sequential code\n- Multiplication: nested\n  loops\n\n1. First\n2. Second\n\nDone.",
    );
    expect(blocks.map((b) => b.kind)).toEqual(["paragraph", "list", "list", "paragraph"]);
    const [, bullets, numbered] = blocks;
    expect(bullets).toMatchObject({ ordered: false });
    expect(bullets?.kind === "list" && bullets.items[1]).toEqual([
      { kind: "text", text: "Multiplication: nested loops" },
    ]);
    expect(numbered).toMatchObject({ ordered: true });
  });

  it("joins wrapped lines and turns headings bold", () => {
    expect(parseAnswer("## Summary\nIt grows\nlinearly.")).toEqual([
      {
        kind: "paragraph",
        inlines: [
          { kind: "bold", children: [{ kind: "text", text: "Summary" }] },
          { kind: "text", text: " It grows linearly." },
        ],
      },
    ]);
  });
});


describe("resolveCitation", () => {
  const source = (lecture: string, label: string | null, start_s: number, end_s: number): Source => ({
    lecture_id: lecture,
    lecture_title: `Lecture ${lecture}`,
    lecture_label: label,
    segment_id: "s001",
    start_s,
    end_s,
    slide_title: null,
    chapter: null,
    score: 1,
  });
  const course = { kind: "course", id: "c" } as const;
  const sources = [source("a", "L1", 80, 120), source("b", "L2", 80, 120)];

  it("finds the lecture a labelled citation names while the answer streams", () => {
    const cite = { label: "[L2 01:30]", seconds: 90, lecture: "L2" };
    expect(resolveCitation(cite, null, sources, course)).toEqual({ lectureId: "b", valid: true, title: "Lecture b" });
    const outside = { label: "[L2 05:00]", seconds: 300, lecture: "L2" };
    expect(resolveCitation(outside, null, sources, course).valid).toBe(false);
  });

  it("trusts the server's check once the answer is saved", () => {
    const cite = { label: "[L1 01:30]", seconds: 90, lecture: "L1" };
    const checked: Citation[] = [{ label: "[L1 01:30]", at_s: 90, lecture_id: "a", segment_id: null, valid: false }];
    expect(resolveCitation(cite, checked, sources, course)).toMatchObject({ lectureId: "a", valid: false });
  });

  it("points a lecture's citations at that lecture", () => {
    const cite = { label: "[01:30]", seconds: 90, lecture: null };
    expect(resolveCitation(cite, null, null, { kind: "lecture", id: "a" })).toEqual({
      lectureId: "a",
      valid: null,
      title: null,
    });
  });
});
