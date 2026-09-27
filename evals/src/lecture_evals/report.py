"""Render study notes as Markdown, for reading a run's output by eye."""

from lecture_core.notes import StudyNotes, format_timestamp


def notes_to_markdown(notes: StudyNotes, title: str) -> str:
    def at(seconds: float) -> str:
        return f"[{format_timestamp(seconds)}]"

    lines = [f"# {title}", "", notes.tldr, "", "## Chapters", ""]
    for chapter in notes.chapters:
        span = f"{format_timestamp(chapter.start_s)}-{format_timestamp(chapter.end_s)}"
        lines += [f"### [{span}] {chapter.title}", "", chapter.summary, ""]

    lines += ["## Key concepts", ""]
    lines += [f"- **{c.term}** {at(c.at_s)}: {c.definition}" for c in notes.concepts]

    lines += ["", "## Formulas", ""]
    if not notes.formulas:
        lines.append("None.")
    lines += [f"- $${f.latex}$$ {at(f.at_s)}: {f.meaning}" for f in notes.formulas]

    lines += ["", "## Quiz", ""]
    for n, q in enumerate(notes.quiz, start=1):
        lines += [f"{n}. {q.question}", f"   - Answer {at(q.at_s)}: {q.answer}"]
    return "\n".join(lines) + "\n"
