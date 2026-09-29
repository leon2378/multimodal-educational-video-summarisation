/** What search and Q&A cover: one lecture, or every lecture in a course. */
export type Scope = { kind: "lecture"; id: string } | { kind: "course"; id: string };

/** Play a lecture from a moment: seek the video when it's the lecture on screen, otherwise
 *  open that lecture's page there. */
export type Open = (lectureId: string, seconds: number) => void;

/** The page that plays a lecture from `seconds`. */
export function lectureHref(lectureId: string, seconds?: number): string {
  return seconds === undefined ? `/lectures/${lectureId}` : `/lectures/${lectureId}?t=${Math.floor(seconds)}`;
}

/** The ?t= start time a lecture page was opened with, if it's a usable number of seconds. */
export function startTime(value: string | string[] | undefined): number | undefined {
  const seconds = Number(Array.isArray(value) ? value[0] : value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined;
}
