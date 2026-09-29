"""Labelled video frames, made without drawing boxes by hand.

For one lecture: find its slides and OCR them (the pipeline's own code), fit where the PDF's
pages sit in the frames (lecture_detector.align), and draw every page into the frame's
geometry. Then each sampled frame is compared with those pages:

- a slide frame (bright, and matching a page closely, or less closely but the same page OCR
  found for that stretch of video: a slide mid-build) gets a `slide` box (the slide area) and
  its page's figures and annotations (lecture_detector.regions) mapped into the frame. Tables
  count as figures: the LLM found 2 in 117 pages of MIT 6.0001, too few to learn or score. A
  region the frame doesn't show yet, because the slide builds up bullet by bullet, is dropped;
- a camera frame (dark, with no slide title band) gets no slide box;
- any other frame is left out rather than labelled wrong: a slide playing a video, or a screen
  that isn't in the PDF (a code demo).

Every frame kept gets a `person` box for each person a COCO-trained detector finds. Frames of
one page are nearly identical, so each page gives a few frames, spread out in time.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image

from lecture_core.timeline import SlideSpan
from lecture_detector.align import Alignment, align
from lecture_detector.pdf import Box, SlidePdf
from lecture_detector.regions import Region, RegionsFile
from lecture_perception import media
from lecture_perception.ocr import SlideOCR, slide_area
from lecture_perception.slides import DetectorConfig, detect_slides, is_slide

Label = Literal["slide", "person", "figure", "annotation"]
LABELS: tuple[Label, ...] = ("slide", "person", "figure", "annotation")
# Finds people in a frame: boxes in frame pixels.
PersonFinder = Callable[[Image.Image], list[Box]]


@dataclass(frozen=True)
class Sampling:
    # How often to look at the video.
    every_s: float = 2.0
    # At most this many frames of each page, at least `page_gap_s` apart.
    per_page: int = 6
    page_gap_s: float = 10.0
    # One camera frame per this many seconds of camera shots.
    camera_every_s: float = 20.0
    # A slide frame must match its page at least this well (correlation of the blurred images;
    # on Lecture 12, slide frames mostly score 0.6 to 0.9 and camera frames under 0.5), or at
    # least `min_agreeing_match` if it's the page OCR matched for that stretch of video: slides
    # that build up score lower. Screens that aren't in the PDF (code demos) score under 0.2.
    min_match: float = 0.6
    min_agreeing_match: float = 0.35
    # A frame whose title band is brighter than this isn't a camera shot.
    title_bright: float = 0.6
    # A region the frame shows less than this share of (by ink) isn't labelled: it's still to
    # be revealed, or covered.
    min_visible: float = 0.5
    # Boxes smaller than this (pixels, either side) aren't labelled.
    min_side_px: float = 6.0


@dataclass
class LabelledFrame:
    time_s: float
    kind: Literal["slide", "camera"]
    image: Image.Image
    boxes: list[tuple[Label, Box]] = field(default_factory=list)
    page: int | None = None  # 1-based


def ink(image: np.ndarray) -> np.ndarray:
    """Dark or strongly coloured pixels of an RGB image scaled to 0-1."""
    gray = image.mean(axis=2)
    saturation = image.max(axis=2) - image.min(axis=2)
    mask: np.ndarray = (gray < 0.7) | (saturation > 0.35)
    return mask


def visible_share(frame_ink: np.ndarray, page_ink: np.ndarray, box: Box) -> float:
    """How much of the page's ink inside the box the frame also has there."""
    left, top, right, bottom = (round(v) for v in box)
    expected = page_ink[top:bottom, left:right]
    if expected.sum() == 0:
        return 0.0
    return float((frame_ink[top:bottom, left:right] & expected).sum() / expected.sum())


def clip(box: Box, area: tuple[int, int, int, int]) -> Box:
    left, top, right, bottom = area
    return (
        max(box[0], left),
        max(box[1], top),
        min(box[2], right),
        min(box[3], bottom),
    )


def span_at(time_s: float, spans: Sequence[SlideSpan]) -> SlideSpan | None:
    return next((s for s in spans if s.start_s <= time_s < s.end_s), None)


class Quota:
    """At most `per_page` frames of a page, `gap_s` apart."""

    def __init__(self, per_page: int, gap_s: float) -> None:
        self.per_page = per_page
        self.gap_s = gap_s
        self.taken: Counter[int] = Counter()
        self.last: dict[int, float] = {}

    def take(self, page: int, time_s: float) -> bool:
        if self.taken[page] >= self.per_page:
            return False
        if page in self.last and time_s - self.last[page] < self.gap_s:
            return False
        self.taken[page] += 1
        self.last[page] = time_s
        return True


class PageMatcher:
    """Compares a frame with every page, drawn where the video shows it."""

    def __init__(
        self, pdf: SlidePdf, alignment: Alignment, area: tuple[int, int, int, int]
    ) -> None:
        self.alignment = alignment
        self.area = area
        self.warped = [self._warp(pdf, alignment, i) for i in range(len(pdf))]
        left, top, right, bottom = area
        self.mask = np.zeros(self.warped[0].shape[:2], dtype=bool)
        self.mask[top:bottom, left:right] = True
        self.title = np.zeros_like(self.mask)
        self.title[top : top + int(0.28 * (bottom - top)), left:right] = True
        self.vectors = np.stack([self._vector(page.mean(axis=2)) for page in self.warped])

    @staticmethod
    def _warp(pdf: SlidePdf, alignment: Alignment, index: int) -> np.ndarray:
        rendered = np.asarray(pdf.render(index), dtype=np.float32) / 255
        warped: np.ndarray = cv2.warpAffine(
            rendered,
            alignment.matrix.astype(np.float32),
            (480, 360),
            flags=cv2.INTER_AREA,
            borderValue=(1.0, 1.0, 1.0),
        )
        return warped

    def _vector(self, gray: np.ndarray) -> np.ndarray:
        # Blurred, so the layout counts rather than exact strokes (the video is blurrier).
        blurred = cv2.GaussianBlur(gray, (0, 0), 3.0)[self.mask]
        blurred = blurred - blurred.mean()
        normalised: np.ndarray = blurred / (np.linalg.norm(blurred) + 1e-6)
        return normalised

    def match(self, gray: np.ndarray) -> tuple[int, float]:
        """The best page (0-based) and its correlation."""
        scores = self.vectors @ self._vector(gray)
        best = int(scores.argmax())
        return best, float(scores[best])

    def title_brightness(self, gray: np.ndarray) -> float:
        return float(gray[self.title].mean())


@dataclass
class LectureFrames:
    alignment: Alignment
    frames: Iterator[LabelledFrame]
    # Why frames were left out, counted as the iterator runs.
    skipped: Counter[str] = field(default_factory=Counter)


def labelled_frames(
    video: Path,
    pdf: SlidePdf,
    regions: RegionsFile,
    people: PersonFinder | None = None,
    sampling: Sampling | None = None,
) -> LectureFrames:
    sampling = sampling or Sampling()
    config = DetectorConfig()
    info = media.probe(video)
    detection = detect_slides(media.sample_frames(video, 1.0), info.duration_s, config)
    ocr = SlideOCR()
    alignment = align([ocr.read(s.id, s.image) for s in detection.slides], pdf)
    areas = np.array([slide_area(s.image) for s in detection.slides])
    left, top, right, bottom = (int(v) for v in np.median(areas, axis=0))
    area = (left, top, right, bottom)
    matcher = PageMatcher(pdf, alignment, area)
    by_page = {p.page: p.regions for p in regions.pages}
    skipped: Counter[str] = Counter()

    def frames() -> Iterator[LabelledFrame]:
        quota = Quota(sampling.per_page, sampling.page_gap_s)
        last_camera = -sampling.camera_every_s
        for time_s, image in media.sample_frames(video, 1 / sampling.every_s):
            gray8 = np.asarray(image.convert("L"))
            gray = gray8.astype(np.float32) / 255
            if is_slide(gray8, config):
                index, score = matcher.match(gray)
                span = span_at(time_s, detection.spans)
                ocr_page = alignment.pages.get(span.slide_id) if span is not None else None
                agrees = index == ocr_page and score >= sampling.min_agreeing_match
                if score < sampling.min_match and not agrees:
                    skipped["bright, matching no page"] += 1
                    continue
                if not quota.take(index, time_s):
                    continue
                frame = _slide_frame(time_s, image, index, matcher, by_page, sampling)
            elif matcher.title_brightness(gray) < sampling.title_bright:
                if time_s - last_camera < sampling.camera_every_s:
                    continue
                last_camera = time_s
                frame = LabelledFrame(time_s, "camera", image)
            else:
                skipped["dark, with a bright title band"] += 1
                continue
            if people is not None:
                frame.boxes += [
                    ("person", box) for box in people(image) if _big_enough(box, sampling)
                ]
            yield frame

    return LectureFrames(alignment, frames(), skipped)


def _slide_frame(
    time_s: float,
    image: Image.Image,
    index: int,
    matcher: PageMatcher,
    by_page: dict[int, list[Region]],
    sampling: Sampling,
) -> LabelledFrame:
    area = matcher.area
    slide_box = (float(area[0]), float(area[1]), float(area[2]), float(area[3]))
    frame = LabelledFrame(time_s, "slide", image, [("slide", slide_box)], index + 1)
    expected = ink(matcher.warped[index])
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    # A pixel of slack: the video is blurrier than the rendered page.
    seen = cv2.dilate(ink(rgb).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    for region in by_page.get(index + 1, []):
        box = clip(matcher.alignment.box(region.box), area)
        if not _big_enough(box, sampling):
            continue
        if visible_share(seen, expected, box) >= sampling.min_visible:
            label: Label = "annotation" if region.label == "annotation" else "figure"
            frame.boxes.append((label, box))
    return frame


def _big_enough(box: Box, sampling: Sampling) -> bool:
    return box[2] - box[0] >= sampling.min_side_px and box[3] - box[1] >= sampling.min_side_px
