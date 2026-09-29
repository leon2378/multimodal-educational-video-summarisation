"""Where a slide PDF's pages sit in a lecture's video frames.

Each slide found in the video is matched to the page it shares most words with, using the
slide's OCR. Lines that read the same in both give pairs of points, page to frame, and one affine
transform is fitted to all of them with RANSAC: a screen-captured lecture shows every page the
same way. On MIT 6.0001 Lecture 10 that's 24 of 24 slides matched and a median error under a
pixel; the transform squeezes the page horizontally (the video has 4:3 pixels) and crops its
margins.

Slides projected in a room would need a transform per frame (a homography); these recordings
don't have any.
"""

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from lecture_detector.pdf import Box, TextLine, words
from lecture_perception.ocr import OcrLine, SlideOcr


class PageText(Protocol):
    """What alignment needs of a slide PDF (lecture_detector.pdf.SlidePdf)."""

    path: Path

    def __len__(self) -> int: ...

    def text_lines(self, index: int) -> list[TextLine]: ...


@dataclass(frozen=True)
class Alignment:
    # 2x3: frame pixel = matrix @ (page x, page y, 1), page points from the top left.
    matrix: np.ndarray
    # Page index (0-based) for each slide id.
    pages: dict[int, int]
    points: int
    inliers: int
    median_error_px: float

    def box(self, box: Box) -> Box:
        """A page box in frame pixels (the bounding box of its mapped corners)."""
        left, top, right, bottom = box
        corners = np.array([[left, top], [right, top], [right, bottom], [left, bottom]])
        mapped = corners @ self.matrix[:, :2].T + self.matrix[:, 2]
        return (
            float(mapped[:, 0].min()),
            float(mapped[:, 1].min()),
            float(mapped[:, 0].max()),
            float(mapped[:, 1].max()),
        )


def best_page(lines: Sequence[OcrLine], page_words: Sequence[set[str]]) -> int:
    """The page whose words overlap the slide's most (Jaccard)."""
    seen = set(words(" ".join(line.text for line in lines)))

    def overlap(index: int) -> float:
        union = seen | page_words[index]
        return len(seen & page_words[index]) / len(union) if union else 0.0

    return max(range(len(page_words)), key=overlap)


def line_pairs(
    lines: Sequence[OcrLine], page_lines: Sequence[TextLine], min_ratio: float = 0.9
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Point pairs (page, frame) from OCR lines that read like a page line: the left edge's
    top and bottom, and the middle of the right edge."""
    targets = [(" ".join(words(line.text)), line.box) for line in page_lines]
    pairs = []
    for line in lines:
        text = " ".join(words(line.text))
        if len(text) < 6 or not targets:
            continue
        ratio, box = max(
            ((difflib.SequenceMatcher(None, text, target).ratio(), box) for target, box in targets),
            key=lambda match: match[0],
        )
        if ratio < min_ratio:
            continue
        xs = [x for x, _ in line.box]
        ys = [y for _, y in line.box]
        left, top, right, bottom = box
        pairs += [
            ((left, top), (min(xs), min(ys))),
            ((left, bottom), (min(xs), max(ys))),
            ((right, (top + bottom) / 2), (max(xs), (min(ys) + max(ys)) / 2)),
        ]
    return pairs


def align(slides: Sequence[SlideOcr], pdf: PageText) -> Alignment:
    page_lines = [pdf.text_lines(i) for i in range(len(pdf))]
    page_words = [set(words(" ".join(line.text for line in lines))) for lines in page_lines]
    pages: dict[int, int] = {}
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for slide in slides:
        page = best_page(slide.lines, page_words)
        pages[slide.slide_id] = page
        pairs += line_pairs(slide.lines, page_lines[page])
    if len(pairs) < 6:
        raise ValueError(f"too few matching text lines to align {pdf.path.name} ({len(pairs)})")
    src = np.array([p for p, _ in pairs], dtype=np.float32)
    dst = np.array([f for _, f in pairs], dtype=np.float32)
    matrix, mask = cv2.estimateAffine2D(src, dst, ransacReprojThreshold=3.0)
    if matrix is None:
        raise ValueError(f"couldn't fit a transform for {pdf.path.name}")
    inlier = mask.ravel().astype(bool)
    errors = np.linalg.norm(dst - (src @ matrix[:, :2].T + matrix[:, 2]), axis=1)
    return Alignment(
        matrix=matrix,
        pages=pages,
        points=len(pairs),
        inliers=int(inlier.sum()),
        median_error_px=float(np.median(errors[inlier])),
    )
