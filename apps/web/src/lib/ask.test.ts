import { describe, expect, it } from "vitest";

import { takeEvents } from "./ask";

describe("takeEvents", () => {
  it("returns whole events and keeps a partial one for later", () => {
    const [events, rest] = takeEvents(
      'data: {"type":"delta","text":"Hel"}\n\ndata: {"type":"delta","text":"lo"}\n\ndata: {"type":"de',
    );
    expect(events).toEqual([
      { type: "delta", text: "Hel" },
      { type: "delta", text: "lo" },
    ]);
    expect(rest).toBe('data: {"type":"de');
  });

  it("finishes an event split across chunks", () => {
    const [first, rest] = takeEvents('data: {"type":"delta",');
    expect(first).toEqual([]);
    const [second, none] = takeEvents(rest + '"text":"x"}\n\n');
    expect(second).toEqual([{ type: "delta", text: "x" }]);
    expect(none).toBe("");
  });
});
