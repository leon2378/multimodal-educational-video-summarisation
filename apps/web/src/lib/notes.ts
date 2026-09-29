import type { components } from "./api/schema";
import { formatTime } from "./timeline";

type StudyNotes = components["schemas"]["StudyNotes"];

/** Study notes as Markdown with [mm:ss] citations, the same layout as the pipeline's
 *  lecture_core.notes.to_markdown, for downloading. */
export function notesToMarkdown(notes: StudyNotes, title: string): string {
  const at = (seconds: number) => `[${formatTime(seconds)}]`;
  const lines = [`# ${title}`, "", notes.tldr, "", "## Chapters", ""];
  for (const chapter of notes.chapters) {
    const span = `${formatTime(chapter.start_s)}-${formatTime(chapter.end_s)}`;
    lines.push(`### [${span}] ${chapter.title}`, "", chapter.summary, "");
  }

  lines.push("## Key concepts", "");
  lines.push(...notes.concepts.map((c) => `- **${c.term}** ${at(c.at_s)}: ${c.definition}`));

  lines.push("", "## Formulas", "");
  if (notes.formulas.length === 0) lines.push("None.");
  lines.push(...notes.formulas.map((f) => `- $$${f.latex}$$ ${at(f.at_s)}: ${f.meaning}`));

  lines.push("", "## Quiz", "");
  notes.quiz.forEach((q, i) => lines.push(`${i + 1}. ${q.question}`, `   - Answer ${at(q.at_s)}: ${q.answer}`));
  return `${lines.join("\n")}\n`;
}

/** A file name for a title: letters, digits and dashes only. */
export function slugify(title: string): string {
  const slug = title
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "lecture";
}
