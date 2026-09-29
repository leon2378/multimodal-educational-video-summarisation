/** Answers arrive as a little Markdown with [mm:ss] citations ([L2 mm:ss] across a course, where
 *  L2 names one of the course's lectures). This parses the part of Markdown answers use
 *  (paragraphs, lists, bold, code, inline LaTeX) into blocks the chat renders as text nodes, so
 *  an answer can never inject HTML. It copes with text that is still streaming: an unclosed **
 *  or $ stays plain text until it closes. */

import type { Citation, Source } from "./api";
import type { Scope } from "./scope";

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; children: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "math"; tex: string }
  | { kind: "cite"; label: string; seconds: number; lecture: string | null };

export type Block =
  | { kind: "paragraph"; inlines: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] };

const MARKUP = /\*\*[^*\n]+\*\*|`[^`\n]+`|\$[^$\n]+\$/.source;
// A citation, optionally naming its lecture, or several in one bracket, which models sometimes
// write: [12:34], [L2 12:34], [12:34, 12:50]. A time without a label takes the one before it.
const ITEM = /(?:L\d+\s+)?\d{1,3}:\d{2}(?::\d{2})?/.source;
const CITATION = String.raw`\[${ITEM}(?:\s*[,;]\s*${ITEM})*\]`;
const TOKEN = new RegExp(`(${MARKUP}|${CITATION})`);
const BULLET = /^\s*(?:[-*•])\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

/** Seconds from a citation label like "[12:34]" or "[1:02:03]" (lecture_core.notes). */
export function citationSeconds(label: string): number | null {
  const parts = label.replace(/^\[|\]$/g, "").split(":").map(Number);
  if (parts.some((n) => !Number.isInteger(n)) || parts.length < 2 || parts.length > 3) return null;
  const seconds = parts.at(-1) ?? 0;
  const minutes = parts.at(-2) ?? 0;
  if (seconds >= 60 || (parts.length === 3 && minutes >= 60)) return null;
  return (parts.length === 3 ? (parts[0] ?? 0) * 3600 : 0) + minutes * 60 + seconds;
}

export function parseInline(text: string, allowBold = true): Inline[] {
  const inlines: Inline[] = [];
  for (const piece of text.split(TOKEN)) {
    if (!piece) continue;
    if (piece.startsWith("**") && piece.endsWith("**") && piece.length > 4 && allowBold) {
      inlines.push({ kind: "bold", children: parseInline(piece.slice(2, -2), false) });
    } else if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
      inlines.push({ kind: "code", text: piece.slice(1, -1) });
    } else if (piece.startsWith("$") && piece.endsWith("$") && piece.length > 2) {
      inlines.push({ kind: "math", tex: piece.slice(1, -1) });
    } else if (piece.startsWith("[") && piece.endsWith("]")) {
      let lecture: string | null = null;
      for (const item of piece.slice(1, -1).split(/\s*[,;]\s*/)) {
        const [, named, time = ""] = /^(?:(L\d+)\s+)?(.+)$/.exec(item) ?? [];
        lecture = named ?? lecture;
        const label = lecture ? `[${lecture} ${time}]` : `[${time}]`;
        const seconds = citationSeconds(`[${time}]`);
        inlines.push(seconds === null ? { kind: "text", text: label } : { kind: "cite", label, seconds, lecture });
      }
    } else {
      inlines.push({ kind: "text", text: piece });
    }
  }
  return withoutRepeatedCitations(inlines);
}

/** Drops a citation that repeats the one just before it ("[34:54] [34:54]"). */
function withoutRepeatedCitations(inlines: Inline[]): Inline[] {
  const kept: Inline[] = [];
  for (const inline of inlines) {
    const last = kept.at(-1);
    const beforeSpace = kept.at(-2);
    if (
      inline.kind === "cite" &&
      last?.kind === "text" &&
      last.text.trim() === "" &&
      beforeSpace?.kind === "cite" &&
      beforeSpace.label === inline.label
    ) {
      kept.pop();
      continue;
    }
    if (inline.kind === "cite" && last?.kind === "cite" && last.label === inline.label) continue;
    kept.push(inline);
  }
  return kept;
}

export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", inlines: parseInline(paragraph.join(" ")) });
    if (list) blocks.push({ kind: "list", ordered: list.ordered, items: list.items.map((item) => parseInline(item)) });
    paragraph = [];
    list = null;
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const ordered = NUMBERED.test(raw);
    if (!line) {
      flush();
    } else if (BULLET.test(raw) || ordered) {
      const item = raw.replace(ordered ? NUMBERED : BULLET, "").trim();
      if (paragraph.length || (list && list.ordered !== ordered)) flush();
      list ??= { ordered, items: [] };
      list.items.push(item);
    } else if (list && /^\s+/.test(raw)) {
      // An indented line continues the last list item.
      list.items[list.items.length - 1] += ` ${line}`;
    } else {
      if (list) flush();
      // Headings read as a bold line.
      const heading = /^#{1,6}\s+(.*)$/.exec(line);
      paragraph.push(heading ? `**${heading[1]}**` : line);
    }
  }
  flush();
  return blocks;
}

/** Where a citation points, and whether that's inside what was retrieved: the server's check
 *  once the answer is saved, or the sources while it streams (and for a time the server didn't
 *  list). Across a course, the label (L2) names the lecture. */
export function resolveCitation(
  cite: { label: string; seconds: number; lecture: string | null },
  citations: Citation[] | null,
  sources: Source[] | null,
  scope: Scope,
): { lectureId: string | null; valid: boolean | null; title: string | null } {
  const checked = citations?.find((c) => c.label === cite.label);
  const containing = sources?.find(
    (s) =>
      (cite.lecture === null || s.lecture_label === cite.lecture) &&
      s.start_s - 1 <= cite.seconds &&
      cite.seconds <= s.end_s + 1,
  );
  const lectureId = checked?.lecture_id ?? containing?.lecture_id ?? (scope.kind === "lecture" ? scope.id : null);
  const valid = checked ? checked.valid : sources ? containing !== undefined : null;
  const title = sources?.find((s) => s.lecture_id === lectureId)?.lecture_title ?? null;
  return { lectureId, valid, title };
}
