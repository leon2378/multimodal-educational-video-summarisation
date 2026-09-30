import { API_URL, type AskEvent } from "./api";
import type { Scope } from "./scope";
import { streamEvents } from "./stream";

/** Asks a question and calls `onEvent` for each event as the answer streams: start, sources,
 *  deltas, then done or error. The endpoint is a POST, so this reads the stream with fetch
 *  rather than EventSource. Throws an ApiError if the question is refused: 401 signed out, 429
 *  a limit (with how long to wait). */
export async function askQuestion(
  scope: Scope,
  question: string,
  threadId: string | null,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const owner = scope.kind === "lecture" ? "lectures" : "courses";
  await streamEvents<AskEvent>(
    `${API_URL}/v1/${owner}/${scope.id}/ask`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(threadId ? { question, thread_id: threadId } : { question }),
      signal,
    },
    onEvent,
  );
}
