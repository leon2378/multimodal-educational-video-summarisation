"""Slide-change detection from sampled frames.

Lecture recordings cut between the slides and a camera on the lecturer, so each frame is first
classified as slide or camera. Only slide frames can change the current slide, and camera shots
in between belong to whichever slide was last shown.

The classifier is a brightness heuristic that works for light slide themes (MIT OCW's are white
on a dark lecture hall). The Phase 5 detector replaces it with a model that also crops the slide
and masks the presenter.

Known limitation: two slides with the same template and line layout, differing only in their
words, can merge into one, because at this hash resolution they look like a build step. In
6.0001 Lecture 10 the "Law of Addition" and "Law of Multiplication" slides (~33:40 and ~35:28)
merge this way. Cropping and aligning the slide region first (Phase 5) should separate them.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from lecture_core.timeline import SlideSpan


@dataclass(frozen=True)
class DetectorConfig:
    # A pixel this bright (0-255) counts as slide background.
    bright_level: int = 200
    # A frame whose centre is at least this share bright is a slide. On MIT OCW lectures slides
    # measure above 0.7 and camera shots below 0.1.
    slide_bright_share: float = 0.5
    # Share of hash bits that must differ for a frame to be a different slide.
    change_share: float = 0.12
    # A new slide must hold for this many consecutive slide samples, which drops transitions.
    min_stable_samples: int = 2
    # A new slide this close to an earlier one is that slide shown again.
    revisit_share: float = 0.06


@dataclass
class DetectedSlide:
    id: int
    image: Image.Image
    first_seen_s: float
    hash: np.ndarray = field(repr=False)


@dataclass
class Detection:
    slides: list[DetectedSlide]
    spans: list[SlideSpan]
    # Share of samples that showed a slide rather than the camera.
    slide_share: float


def is_slide(gray: np.ndarray, config: DetectorConfig) -> bool:
    height, width = gray.shape
    centre = gray[height // 8 : height - height // 8, width // 6 : width - width // 6]
    return float((centre > config.bright_level).mean()) >= config.slide_bright_share


def difference_hash(gray: np.ndarray, size: int = 16) -> np.ndarray:
    """256-bit dHash: is each pixel brighter than its right-hand neighbour, on a 17x16
    thumbnail. Robust to compression noise and small brightness shifts, but sensitive to
    text changes, which is what separates two slides that share a template."""
    thumbnail = Image.fromarray(gray).resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(thumbnail, dtype=np.int16)
    return (pixels[:, 1:] > pixels[:, :-1]).flatten()


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """Share of hash bits that differ."""
    return float(np.count_nonzero(a != b)) / a.size


@dataclass
class _Candidate:
    hash: np.ndarray
    image: Image.Image
    first_s: float
    samples: int = 1


def detect_slides(
    frames: Iterable[tuple[float, Image.Image]],
    duration_s: float,
    config: DetectorConfig | None = None,
) -> Detection:
    config = config or DetectorConfig()
    slides: list[DetectedSlide] = []
    spans: list[SlideSpan] = []
    current: DetectedSlide | None = None
    candidate: _Candidate | None = None
    samples = slide_samples = 0

    for time_s, image in frames:
        samples += 1
        gray = np.asarray(image.convert("L"))
        if not is_slide(gray, config):
            continue
        slide_samples += 1
        frame_hash = difference_hash(gray)

        if current is not None and distance(frame_hash, current.hash) < config.change_share:
            # Same slide, perhaps with another bullet revealed: keep the most complete frame.
            current.image, current.hash = image, frame_hash
            candidate = None
            continue

        if candidate is not None and distance(frame_hash, candidate.hash) < config.change_share:
            candidate.samples += 1
            candidate.image, candidate.hash = image, frame_hash
        else:
            candidate = _Candidate(frame_hash, image, time_s)

        if candidate.samples >= config.min_stable_samples:
            current = _accept(candidate, slides, config)
            if spans:
                spans[-1].end_s = candidate.first_s
            spans.append(
                SlideSpan(slide_id=current.id, start_s=candidate.first_s, end_s=duration_s)
            )
            candidate = None

    return Detection(
        slides=slides,
        spans=spans,
        slide_share=slide_samples / samples if samples else 0.0,
    )


def _accept(
    candidate: _Candidate, slides: list[DetectedSlide], config: DetectorConfig
) -> DetectedSlide:
    for earlier in slides:
        if distance(candidate.hash, earlier.hash) < config.revisit_share:
            earlier.image, earlier.hash = candidate.image, candidate.hash
            return earlier
    slide = DetectedSlide(len(slides), candidate.image, candidate.first_s, candidate.hash)
    slides.append(slide)
    return slide
