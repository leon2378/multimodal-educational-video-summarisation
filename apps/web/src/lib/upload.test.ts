import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { PartError, type PartsUpload, type Progress, type Transport, limitFrom, partRange, uploadInParts } from "./upload";

const MiB = 1024 * 1024;

describe("partRange", () => {
  const plan = { size_bytes: 40 * MiB, part_bytes: 16 * MiB };

  it("cuts the file into parts of part_bytes, the last one shorter", () => {
    expect(partRange(plan, 1)).toEqual([0, 16 * MiB]);
    expect(partRange(plan, 2)).toEqual([16 * MiB, 32 * MiB]);
    expect(partRange(plan, 3)).toEqual([32 * MiB, 40 * MiB]);
  });

  it("has a full last part when the size divides evenly", () => {
    expect(partRange({ size_bytes: 32 * MiB, part_bytes: 16 * MiB }, 2)).toEqual([16 * MiB, 32 * MiB]);
  });
});

/** Storage as the API reports it: the parts it has, and URLs for the rest. Each URL carries a
 *  round number, so an expired one can be told from a fresh one. */
function fakeStorage(size: number, part: number, has: number[] = []) {
  const stored = new Set(has);
  const count = Math.max(1, Math.ceil(size / part));
  let round = 0;
  const sent: { number: number; bytes: number }[] = [];
  const transport: Transport = {
    plan: vi.fn(async (): Promise<PartsUpload> => {
      round += 1;
      const missing = Array.from({ length: count }, (_, i) => i + 1).filter((n) => !stored.has(n));
      return {
        size_bytes: size,
        part_bytes: part,
        count,
        uploaded: [...stored].sort((a, b) => a - b),
        parts: missing.map((number) => ({ number, url: `part-${number}-round-${round}` })),
        expires_in_s: 3600,
      };
    }),
    complete: vi.fn(async () => stored.size === count),
    put: vi.fn(async (url: string, body: Blob, onSent: (bytes: number) => void) => {
      const number = Number(url.split("-")[1]);
      onSent(body.size);
      sent.push({ number, bytes: body.size });
      stored.add(number);
    }),
    wait: vi.fn(async () => undefined),
  };
  return { transport, stored, sent };
}

const file = (size: number) => new Blob([new Uint8Array(size)]);
const run = (blob: Blob, transport: Transport, extra: Partial<Parameters<typeof uploadInParts>[2]> = {}) => {
  const progress: Progress[] = [];
  const promise = uploadInParts(blob, transport, {
    signal: new AbortController().signal,
    onProgress: (p) => progress.push(p),
    ...extra,
  });
  return { promise, progress };
};

describe("uploadInParts", () => {
  it("sends each part with its exact length, then joins them", async () => {
    const { transport, sent } = fakeStorage(40, 16);
    const { promise, progress } = run(file(40), transport);
    await promise;
    expect(sent.sort((a, b) => a.number - b.number)).toEqual([
      { number: 1, bytes: 16 },
      { number: 2, bytes: 16 },
      { number: 3, bytes: 8 },
    ]);
    expect(transport.complete).toHaveBeenCalledTimes(1);
    expect(progress.at(-1)).toEqual({ loaded: 40, total: 40, resumed: 0 });
  });

  it("resumes: sends only the parts storage doesn't have, and counts the rest as done", async () => {
    const { transport, sent } = fakeStorage(40, 16, [1, 3]);
    const { promise, progress } = run(file(40), transport);
    await promise;
    expect(sent).toEqual([{ number: 2, bytes: 16 }]);
    expect(progress[0]).toEqual({ loaded: 24, total: 40, resumed: 24 });
  });

  it("tries a part again after a pause when it doesn't get there", async () => {
    const { transport, sent } = fakeStorage(16, 16);
    const put = transport.put;
    transport.put = vi
      .fn()
      .mockRejectedValueOnce(new PartError(0))
      .mockRejectedValueOnce(new PartError(503))
      .mockImplementation(put);
    await run(file(16), transport).promise;
    expect(transport.put).toHaveBeenCalledTimes(3);
    expect(vi.mocked(transport.wait).mock.calls.map(([ms]) => ms)).toEqual([1000, 2000]);
    expect(sent).toHaveLength(1);
    expect(transport.plan).toHaveBeenCalledTimes(1);
  });

  it("asks for fresh URLs when one has expired (403), rather than trying it again", async () => {
    const { transport } = fakeStorage(32, 16);
    const put = transport.put;
    transport.put = vi.fn(async (url: string, body: Blob, onSent: (bytes: number) => void, signal: AbortSignal) => {
      if (url === "part-2-round-1") throw new PartError(403);
      return put(url, body, onSent, signal);
    });
    await run(file(32), transport).promise;
    expect(transport.plan).toHaveBeenCalledTimes(2);
    expect(vi.mocked(transport.put).mock.calls.map(([url]) => url).sort()).toEqual([
      "part-1-round-1",
      "part-2-round-1",
      "part-2-round-2",
    ]);
    expect(transport.complete).toHaveBeenCalledTimes(1);
  });

  it("sends what's missing when joining finds a part isn't there", async () => {
    const { transport, stored } = fakeStorage(32, 16);
    // Storage lost part 2 after it was sent, the first time only.
    transport.complete = vi.fn().mockImplementationOnce(async () => {
      stored.delete(2);
      return false;
    }).mockResolvedValue(true);
    await run(file(32), transport).promise;
    expect(transport.plan).toHaveBeenCalledTimes(2);
    expect(vi.mocked(transport.put).mock.calls.map(([url]) => url)).toContain("part-2-round-2");
  });

  it("gives up after a few rounds", async () => {
    const { transport } = fakeStorage(16, 16);
    transport.put = vi.fn().mockRejectedValue(new PartError(0));
    await expect(run(file(16), transport, { rounds: 2, tries: 2 }).promise).rejects.toThrow(
      "Part of the video couldn't be uploaded.",
    );
    expect(transport.plan).toHaveBeenCalledTimes(2);
    expect(transport.complete).not.toHaveBeenCalled();
  });

  it("stops when cancelled", async () => {
    const { transport } = fakeStorage(64, 16);
    const controller = new AbortController();
    transport.put = vi.fn(async () => {
      controller.abort();
      throw new DOMException("Aborted", "AbortError");
    });
    const { promise, progress } = run(file(64), transport, { signal: controller.signal });
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(transport.complete).not.toHaveBeenCalled();
    // Nothing reported after the cancel, which would show the upload as going again.
    expect(progress).toEqual([{ loaded: 0, total: 64, resumed: 0 }]);
  });

  it("has at most `parallel` parts on their way at once", async () => {
    const { transport } = fakeStorage(160, 16);
    const put = transport.put;
    let busy = 0;
    let most = 0;
    transport.put = vi.fn(async (...args: Parameters<Transport["put"]>) => {
      busy += 1;
      most = Math.max(most, busy);
      await new Promise((resolve) => setTimeout(resolve, 1));
      busy -= 1;
      return put(...args);
    });
    await run(file(160), transport, { parallel: 3 }).promise;
    expect(transport.put).toHaveBeenCalledTimes(10);
    expect(most).toBe(3);
  });
});

describe("limitFrom", () => {
  it("reads the limit from the API's 413", () => {
    expect(limitFrom(new ApiError("File is 7000 bytes; the limit is 5000.", 413))).toBe(5000);
    expect(limitFrom(new ApiError("Something else.", 409))).toBeNull();
  });
});
