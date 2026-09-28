import { API_URL, type AskEvent } from "./api";

/** Complete server-sent events at the front of `buffer`, and what's left after them. */
export function takeEvents(buffer: string): [AskEvent[], string] {
  const events: AskEvent[] = [];
  let rest = buffer;
  for (let end = rest.indexOf("\n\n"); end >= 0; end = rest.indexOf("\n\n")) {
    for (const line of rest.slice(0, end).split("\n")) {
      if (line.startsWith("data: ")) events.push(JSON.parse(line.slice(6)) as AskEvent);
    }
    rest = rest.slice(end + 2);
  }
  return [events, rest];
}

/** Asks a question and calls `onEvent` for each event as the answer streams: start, sources,
 *  deltas, then done or error. The endpoint is a POST, so this reads the stream with fetch
 *  rather than EventSource. Throws with the API's message if the question is refused. */
export async function askQuestion(
  lectureId: string,
  question: string,
  threadId: string | null,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/v1/lectures/${lectureId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(threadId ? { question, thread_id: threadId } : { question }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const [events, rest] = takeEvents(buffer + value);
    buffer = rest;
    events.forEach(onEvent);
  }
}
