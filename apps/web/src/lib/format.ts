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

/** A hue from 0 to 359 that's always the same for an id. */
export function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

/** Gradients for covers without a slide, picked to sit with the app's colours: any hue at all
 *  made some muddy (olive, brown). Light to deep, top left to bottom right. */
const COVERS: [string, string][] = [
  ["oklch(0.74 0.12 190)", "oklch(0.5 0.13 245)"], // lagoon to blue
  ["oklch(0.7 0.13 245)", "oklch(0.46 0.16 275)"], // sky to indigo
  ["oklch(0.7 0.14 300)", "oklch(0.5 0.17 335)"], // violet to magenta
  ["oklch(0.76 0.13 35)", "oklch(0.56 0.18 15)"], // coral to rose
  ["oklch(0.83 0.12 80)", "oklch(0.64 0.16 48)"], // amber to orange
  ["oklch(0.76 0.13 160)", "oklch(0.52 0.1 200)"], // mint to teal
  ["oklch(0.8 0.1 220)", "oklch(0.56 0.11 195)"], // ice to cyan
  ["oklch(0.52 0.05 260)", "oklch(0.32 0.05 275)"], // slate to night
];

/** The cover gradient for an id, always the same one. */
export function coverGradient(id: string): string {
  const [from, to] = COVERS[hueFor(id) % COVERS.length] ?? COVERS[0]!;
  return `linear-gradient(135deg, ${from}, ${to})`;
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
