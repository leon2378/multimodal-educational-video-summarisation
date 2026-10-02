import { describe, expect, it } from "vitest";

import { excerpt, highlight } from "./highlight";

const marked = (text: string, query: string) =>
  highlight(text, query)
    .filter((piece) => piece.match)
    .map((piece) => piece.text);

describe("highlight", () => {
  it("marks query words and words they start", () => {
    expect(marked("Big O notation bounds the growth of a program", "big notation grow")).toEqual([
      "Big",
      "notation",
      "growth",
    ]);
  });

  it("keeps every character, in order", () => {
    const text = "Searching a sorted list: binary search.";
    expect(
      highlight(text, "search")
        .map((piece) => piece.text)
        .join(""),
    ).toBe(text);
  });

  it("doesn't match inside a word", () => {
    expect(marked("research and search", "search")).toEqual(["search"]);
  });

  it("skips common words, which would match everywhere", () => {
    expect(marked("Then they time the program with a timer", "why not just time the program")).toEqual([
      "time",
      "program",
      "timer",
    ]);
  });

  it("skips short words and treats regex characters literally", () => {
    expect(highlight("O(n) is a bound", "O(n) a")).toEqual([{ text: "O(n) is a bound", match: false }]);
    expect(marked("costs $5.00 (roughly)", "roughly)")).toEqual(["roughly"]);
  });
});

describe("excerpt", () => {
  const text = "So we started off talking about lists, and then we moved on to the idea of bisection search.";

  it("starts a little before the first match, at a word", () => {
    expect(excerpt(text, "bisection", 20)).toBe("…on to the idea of bisection search.");
  });

  it("keeps the start when the match is near it, or there's none", () => {
    expect(excerpt(text, "lists", 60)).toBe(text);
    expect(excerpt(text, "quicksort", 20)).toBe(text);
    expect(excerpt(text, "the", 20)).toBe(text);
  });
});
