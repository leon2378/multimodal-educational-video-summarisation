"""Build the timeline: align what was said with the slide on screen (blueprint section 4)."""

from dataclasses import dataclass, field

from lecture_core.timeline import (
    SlideDeck,
    SlideReading,
    SlideSpan,
    Timeline,
    TimelineSegment,
    Transcript,
    TranscriptSegment,
)


@dataclass
class _Group:
    span: SlideSpan | None
    speech: list[TranscriptSegment] = field(default_factory=list)


def build_timeline(
    transcript: Transcript,
    deck: SlideDeck,
    readings: list[SlideReading],
    max_segment_s: float = 90.0,
) -> Timeline:
    """One timeline segment per slide span, split at speech boundaries when it runs past
    `max_segment_s`, so every citation points at a short stretch of the lecture. Speech before
    the first slide gets segments with no slide."""
    groups: list[_Group] = []
    for speech in transcript.segments:
        span = _span_at((speech.start_s + speech.end_s) / 2, deck.spans)
        last = groups[-1] if groups else None
        if (
            last is not None
            and last.span is span
            and speech.end_s - last.speech[0].start_s <= max_segment_s
        ):
            last.speech.append(speech)
        else:
            groups.append(_Group(span, [speech]))

    segments = [
        TimelineSegment(
            id=f"s{index:03d}",
            start_s=group.speech[0].start_s,
            end_s=group.speech[-1].end_s,
            transcript=" ".join(s.text for s in group.speech),
            slide_id=group.span.slide_id if group.span else None,
            words=[word for s in group.speech for word in s.words],
        )
        for index, group in enumerate(groups)
    ]
    return Timeline(duration_s=transcript.duration_s, segments=segments, slides=readings)


def _span_at(time_s: float, spans: list[SlideSpan]) -> SlideSpan | None:
    for span in spans:
        if span.start_s <= time_s < span.end_s:
            return span
    return None
