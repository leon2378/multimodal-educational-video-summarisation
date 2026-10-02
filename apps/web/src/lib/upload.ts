/** Uploading a video in parts, each straight to storage, so an interrupted upload continues from
 *  the parts that arrived, even after a reload (docs/adr/0012-resumable-uploads-in-parts.md). */

import { ApiError, api, unwrap } from "./api";
import type { components } from "./api/schema";

export type PartsUpload = components["schemas"]["PartsUpload"];

/** The bytes of part `number` (from 1), as [start, end): every part is `part_bytes` long but
 *  the last. Each part's URL is signed for exactly this length. */
export function partRange(plan: Pick<PartsUpload, "size_bytes" | "part_bytes">, number: number): [number, number] {
  const start = (number - 1) * plan.part_bytes;
  return [start, Math.min(start + plan.part_bytes, plan.size_bytes)];
}

/** A part storage refused (`status`), or that never got there (`status` 0). */
export class PartError extends Error {
  constructor(readonly status: number) {
    super(status ? `Storage refused a part of the upload (HTTP ${status}).` : "Couldn't reach storage.");
    this.name = "PartError";
  }
}

export interface Progress {
  /** Bytes in storage, counting the parts on their way. */
  loaded: number;
  total: number;
  /** What storage already had when this upload started: more than 0 when it's resumed. */
  resumed: number;
}

export interface Transport {
  /** Which parts storage has, and a fresh URL for each of the others. */
  plan: () => Promise<PartsUpload>;
  /** Joins the parts: true once done, false while some are missing. */
  complete: () => Promise<boolean>;
  /** Sends one part, reporting the bytes sent so far. */
  put: (url: string, body: Blob, onSent: (bytes: number) => void, signal: AbortSignal) => Promise<void>;
  wait: (ms: number, signal: AbortSignal) => Promise<void>;
}

export interface Options {
  signal: AbortSignal;
  onProgress: (progress: Progress) => void;
  /** Parts on their way at once. */
  parallel?: number;
  /** Goes at each part before leaving it to the next round. */
  tries?: number;
  /** Rounds of asking for the missing parts and sending them, before giving up. */
  rounds?: number;
}

/** Sends `file` in the parts storage doesn't have yet, a few at a time, then joins them.
 *  A part that fails is tried again after a pause. One that storage refuses with 403 has a URL
 *  that expired, so it waits for the next round, which asks for fresh URLs for the parts still
 *  missing. Rejects with the signal's reason when `signal` aborts. */
export async function uploadInParts(file: Blob, transport: Transport, options: Options): Promise<void> {
  const { signal, onProgress, parallel = 4, tries = 4, rounds = 4 } = options;
  let resumed: number | null = null;

  for (let round = 0; round < rounds; round++) {
    signal.throwIfAborted();
    const plan = await transport.plan();
    const size = (number: number) => {
      const [start, end] = partRange(plan, number);
      return end - start;
    };
    let stored = plan.uploaded.reduce((sum, number) => sum + size(number), 0);
    resumed ??= stored;
    const sending = new Map<number, number>();
    let reported = 0;
    const report = (now = false) => {
      // Nothing after a cancel. Otherwise at most ten times a second, since every part reports
      // as it goes.
      const time = Date.now();
      if (signal.aborted || (!now && time - reported < 100)) return;
      reported = time;
      let loaded = stored;
      for (const bytes of sending.values()) loaded += bytes;
      onProgress({ loaded, total: plan.size_bytes, resumed: resumed ?? 0 });
    };
    report(true);

    let failed = false;
    const queue = [...plan.parts];
    const send = async ({ number, url }: (typeof plan.parts)[number]) => {
      const [start, end] = partRange(plan, number);
      for (let attempt = 1; attempt <= tries; attempt++) {
        try {
          await transport.put(url, file.slice(start, end), (bytes) => {
            sending.set(number, bytes);
            report();
          }, signal);
          sending.delete(number);
          stored += end - start;
          report(true);
          return;
        } catch (error) {
          sending.delete(number);
          report(true);
          signal.throwIfAborted();
          // A 4xx won't change by trying again (a 403 is an expired URL): the next round asks
          // for a fresh one.
          const lasting =
            error instanceof PartError && error.status >= 400 && error.status < 500 && error.status !== 429;
          if (lasting || attempt === tries) break;
          await transport.wait(1000 * 2 ** (attempt - 1), signal);
        }
      }
      failed = true;
    };
    await Promise.all(
      Array.from({ length: Math.min(parallel, queue.length) }, async () => {
        for (let part = queue.shift(); part; part = queue.shift()) await send(part);
      }),
    );
    signal.throwIfAborted();
    if (!failed && (await transport.complete())) return;
  }
  throw new Error("Part of the video couldn't be uploaded.");
}

/** The transport for a lecture's upload: the API for the plan and joining, XHR for the parts,
 *  since fetch can't report an upload's progress. */
export function lectureTransport(lectureId: string, size: number, signal: AbortSignal): Transport {
  const path = { params: { path: { lecture_id: lectureId } }, signal };
  return {
    plan: async () =>
      unwrap(await api.POST("/v1/lectures/{lecture_id}/upload-parts", { ...path, body: { size_bytes: size } })),
    complete: async () => {
      const result = await api.POST("/v1/lectures/{lecture_id}/complete-upload", path);
      // Parts missing. Any other conflict shows when the next round asks for the plan.
      if (result.response.status === 409) return false;
      unwrap(result);
      return true;
    },
    put: putPart,
    wait: (ms, waitSignal) =>
      new Promise((resolve) => {
        const timer = setTimeout(resolve, ms);
        waitSignal.addEventListener("abort", () => {
          clearTimeout(timer);
          resolve();
        });
      }),
  };
}

/** PUT one part with no headers of our own: its URL is signed for its length alone. A slice of
 *  a file has no type, so the browser adds no Content-Type either. */
function putPart(url: string, body: Blob, onSent: (bytes: number) => void, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    signal.addEventListener("abort", abort, { once: true });
    const settle = (error?: unknown) => {
      signal.removeEventListener("abort", abort);
      if (error === undefined) resolve();
      else reject(error);
    };
    request.open("PUT", url);
    request.upload.onprogress = (event) => onSent(event.loaded);
    request.onload = () =>
      settle(request.status >= 200 && request.status < 300 ? undefined : new PartError(request.status));
    request.onerror = () => settle(new PartError(0));
    request.onabort = () => settle(signal.reason ?? new DOMException("Aborted", "AbortError"));
    request.send(body);
  });
}

/** The most a person may upload, read from the API's refusal ("…; the limit is N."). */
export function limitFrom(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.status !== 413) return null;
  const found = /limit is (\d+)/.exec(error.message);
  return found ? Number(found[1]) : null;
}
