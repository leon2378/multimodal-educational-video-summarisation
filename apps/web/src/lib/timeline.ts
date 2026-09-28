/** Pure helpers that keep the page in step with the video. Times are seconds. */

export interface Span {
  start_s: number;
  end_s: number;
}

/** "MM:SS" under an hour and "H:MM:SS" from an hour on, rounding down. The same format as the
 *  notes' citations (lecture_core.notes.format_timestamp). */
export function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = String(Math.floor((whole % 3600) / 60)).padStart(2, "0");
  const secs = String(whole % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}

/** Index of the item playing at `time`: the last one starting at or before it (-1 before the
 *  first). Items must be sorted by start. Binary search, since it runs on every timeupdate. */
export function indexAt(items: readonly { start_s: number }[], time: number): number {
  let low = 0;
  let high = items.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if ((items[middle]?.start_s ?? Infinity) <= time) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found;
}

/** The slide that's current at `time`: the one whose spans cover it, camera shots included. */
export function slideAt(
  slides: readonly { slide_id: number; spans: readonly Span[] }[],
  time: number,
): number | null {
  for (const slide of slides) {
    if (slide.spans.some((span) => span.start_s <= time && time < span.end_s)) {
      return slide.slide_id;
    }
  }
  return null;
}
