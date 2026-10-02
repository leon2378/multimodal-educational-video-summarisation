/** Formatting for people to read. Timestamps that seek the video live in timeline.ts. */

/** A length of time: "45 s", "51 min", "1 h 3 min". */
export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole} s`;
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** Seconds a stage took: "0.4 s", "12 s", "2 min 5 s". */
export function formatSeconds(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const whole = Math.round(seconds);
  const rest = whole % 60;
  return rest ? `${Math.floor(whole / 60)} min ${rest} s` : `${whole / 60} min`;
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${unit === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** US dollars, with enough digits that a fraction of a cent doesn't read as $0.00. */
export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 0.01) return `$${usd.toPrecision(2)}`;
  return `$${usd.toFixed(2)}`;
}

/** "just now", "5 min ago", "yesterday", "3 days ago", then the date. */
export function formatRelative(when: string | Date, now: Date = new Date()): string {
  const date = typeof when === "string" ? new Date(when) : when;
  const seconds = (now.getTime() - date.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  const days = Math.floor(seconds / 86400);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

/** A title to start from, from a video's file name: "lecture_03-sorting.mp4" -> "Lecture 03 sorting". */
export function titleFromFilename(filename: string): string {
  const stem = filename.replace(/\.[^./\\]+$/, "");
  const words = stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : filename;
}

/** A hue from 0 to 359 that's always the same for an id, for covers without a slide. */
export function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

/** Something to call a count: pluralise(1, "lecture") is "1 lecture". */
export function pluralise(count: number, noun: string, plural = `${noun}s`): string {
  return `${count} ${count === 1 ? noun : plural}`;
}

/** Where a lecture from a link came from, as a link to show ("youtube.com"), or null for
 *  anything that isn't a web address: a javascript: link never becomes an href. */
export function sourceSite(url: string | null): string | null {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}
