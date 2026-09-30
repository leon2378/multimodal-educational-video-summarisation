import { errorFrom } from "./api";
import { authHeaders } from "./auth";

/** Complete server-sent events at the front of `buffer`, and what's left after them. */
export function takeEvents<T>(buffer: string): [T[], string] {
  const events: T[] = [];
  let rest = buffer;
  for (let end = rest.indexOf("\n\n"); end >= 0; end = rest.indexOf("\n\n")) {
    for (const line of rest.slice(0, end).split("\n")) {
      if (line.startsWith("data: ")) events.push(JSON.parse(line.slice(6)) as T);
    }
    rest = rest.slice(end + 2);
  }
  return [events, rest];
}

/** Reads a server-sent event stream with fetch, which, unlike EventSource, can send the session
 *  token and POST a body. Calls `onEvent` for each event and returns when the server closes the
 *  stream. Throws an ApiError when the request is refused. */
export async function streamEvents<T>(
  url: string,
  init: RequestInit & { headers?: Record<string, string> },
  onEvent: (event: T) => void,
): Promise<void> {
  const response = await fetch(url, { ...init, headers: { ...init.headers, ...(await authHeaders()) } });
  if (!response.ok || !response.body) {
    throw errorFrom(response, await response.json().catch(() => null));
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const [events, rest] = takeEvents<T>(buffer + value);
    buffer = rest;
    events.forEach(onEvent);
  }
}
