import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { streamEvents, takeEvents } from "./stream";

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

describe("streamEvents", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("calls back for each event until the server closes the stream", async () => {
    const chunks = ['data: {"n":1}\n\ndata: {"n"', ':2}\n\n'];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        controller.close();
      },
    });
    const fetch = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    const seen: unknown[] = [];
    await streamEvents("http://api/v1/lectures/x/events", { headers: { Accept: "text/event-stream" } }, (e) =>
      seen.push(e),
    );

    expect(seen).toEqual([{ n: 1 }, { n: 2 }]);
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ headers: { Accept: "text/event-stream" } });
  });

  it("throws the API's refusal, with how long a limit asks to wait", async () => {
    const refused = new Response(JSON.stringify({ detail: "That's 5 questions in a minute." }), {
      status: 429,
      headers: { "Retry-After": "42" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(refused));

    const error = await streamEvents("http://api/ask", { method: "POST" }, () => {}).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, retryAfterS: 42, message: "That's 5 questions in a minute." });
  });
});
