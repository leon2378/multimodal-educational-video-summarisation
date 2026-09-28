"""What gets indexed: one chunk per timeline segment (blueprint section 4, step 6).

A segment is 30-90 seconds of speech on one slide, so it's already the right size to cite. The
slide's title and text go into the chunk too: technical terms often appear on the slide even
when the speech recognition mangles them.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from lecture_core.notes import Chapter
from lecture_core.timeline import Timeline

# Bump when the chunk text changes, so cached embeddings are recomputed.
CHUNKING_VERSION = "1"


class Chunk(BaseModel):
    segment_id: str
    start_s: float
    end_s: float
    slide_id: int | None
    slide_title: str | None
    transcript: str
    # The slide's title and text, then the transcript: what gets embedded, matched and reranked.
    text: str


def build_chunks(timeline: Timeline) -> list[Chunk]:
    chunks = []
    for segment in timeline.segments:
        slide = timeline.slide(segment.slide_id)
        slide_lines = [f"Slide: {slide.title}", slide.text] if slide else []
        chunks.append(
            Chunk(
                segment_id=segment.id,
                start_s=segment.start_s,
                end_s=segment.end_s,
                slide_id=segment.slide_id,
                slide_title=slide.title if slide else None,
                transcript=segment.transcript,
                text="\n".join(line for line in [*slide_lines, segment.transcript] if line),
            )
        )
    return chunks


def chapter_at(chapters: Sequence[Chapter], at_s: float) -> str | None:
    """The title of the chapter under way at `at_s`, if the notes have one."""
    return next((c.title for c in reversed(chapters) if c.start_s <= at_s), None)
