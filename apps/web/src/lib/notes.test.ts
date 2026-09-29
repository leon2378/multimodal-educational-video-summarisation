import { describe, expect, it } from "vitest";

import { notesToMarkdown, slugify } from "./notes";

describe("notesToMarkdown", () => {
  it("matches the pipeline's layout", () => {
    const markdown = notesToMarkdown(
      {
        tldr: "Measuring efficiency.",
        chapters: [{ title: "Timing", start_s: 0, end_s: 125.5, summary: "Why wall-clock time misleads." }],
        concepts: [{ term: "Big O", definition: "An upper bound on growth.", at_s: 754 }],
        formulas: [{ latex: "O(n^2)", meaning: "Quadratic.", at_s: 3725 }],
        quiz: [{ question: "What does O(1) mean?", answer: "Constant time.", at_s: 61 }],
      },
      "Lecture 10",
    );
    expect(markdown).toBe(
      [
        "# Lecture 10",
        "",
        "Measuring efficiency.",
        "",
        "## Chapters",
        "",
        "### [00:00-02:05] Timing",
        "",
        "Why wall-clock time misleads.",
        "",
        "## Key concepts",
        "",
        "- **Big O** [12:34]: An upper bound on growth.",
        "",
        "## Formulas",
        "",
        "- $$O(n^2)$$ [1:02:05]: Quadratic.",
        "",
        "## Quiz",
        "",
        "1. What does O(1) mean?",
        "   - Answer [01:01]: Constant time.",
        "",
      ].join("\n"),
    );
  });

  it("says when there are no formulas", () => {
    const markdown = notesToMarkdown({ tldr: "", chapters: [], concepts: [], formulas: [], quiz: [] }, "T");
    expect(markdown).toContain("## Formulas\n\nNone.\n");
  });
});

describe("slugify", () => {
  it("keeps letters and digits", () => {
    expect(slugify("Understanding Program Efficiency, Part 1")).toBe("understanding-program-efficiency-part-1");
    expect(slugify("Café résumé")).toBe("cafe-resume");
    expect(slugify("!!!")).toBe("lecture");
  });
});
