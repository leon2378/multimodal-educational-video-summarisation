"""The frame detector's dataset: alignment of PDF pages to frames, labels and COCO splits."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from lecture_detector import dataset
from lecture_detector.align import align, best_page, line_pairs
from lecture_detector.frames import LabelledFrame, Quota, clip, visible_share
from lecture_detector.pdf import TextLine
from lecture_detector.regions import to_page_box
from lecture_perception.ocr import OcrLine, SlideOcr

# Frame pixels from page points: 4:3 pixels squeeze x (0.5 vs 0.667), and the top margin is cut.
MATRIX = np.array([[0.5, 0.0, 40.0], [0.0, 0.667, -24.0]])


def page_line(text: str, top: float, left: float = 60.0, height: float = 20.0) -> TextLine:
    return TextLine(text, (left, top, left + 10.0 * len(text), top + height))


def as_ocr(line: TextLine) -> OcrLine:
    left, top, right, bottom = line.box
    corners = np.array([[left, top], [right, top], [right, bottom], [left, bottom]])
    mapped = corners @ MATRIX[:, :2].T + MATRIX[:, 2]
    return OcrLine(
        text=line.text.upper(), score=0.98, box=[(float(x), float(y)) for x, y in mapped]
    )


@dataclass
class FakePdf:
    pages: list[list[TextLine]]
    path: Path = Path("slides.pdf")

    def __len__(self) -> int:
        return len(self.pages)

    def text_lines(self, index: int) -> list[TextLine]:
        return self.pages[index]


PAGES = [
    [page_line("Understanding program efficiency", 100), page_line("download the slides", 160)],
    [
        page_line("Counting operations", 80),
        page_line("assume these steps take constant time", 150),
        page_line("then count the number of operations", 190),
    ],
    [
        page_line("Linear search on a sorted list", 80),
        page_line("must only look until a greater number", 150),
        page_line("overall complexity is order n", 200),
    ],
]


def slide(slide_id: int, page: int) -> SlideOcr:
    return SlideOcr(
        slide_id=slide_id,
        lines=[as_ocr(line) for line in PAGES[page]],
        area=(0, 0, 480, 360),
        ink_outside_text=0.0,
    )


def test_slides_are_matched_to_pages_and_the_transform_recovered() -> None:
    alignment = align([slide(0, 2), slide(1, 1)], FakePdf(PAGES))

    assert alignment.pages == {0: 2, 1: 1}
    np.testing.assert_allclose(alignment.matrix, MATRIX, atol=1e-3)
    assert alignment.median_error_px < 0.01
    # A page box lands where the frame shows it.
    assert alignment.box((100.0, 300.0, 300.0, 400.0)) == pytest.approx((90.0, 176.1, 190.0, 242.8))


def test_lines_that_dont_read_alike_give_no_points() -> None:
    lines = [as_ocr(page_line("something else entirely", 80))]

    assert line_pairs(lines, PAGES[1]) == []
    assert best_page([as_ocr(PAGES[2][1])], [{"counting"}, {"must", "only", "look"}]) == 1


def test_too_few_matching_lines_is_an_error() -> None:
    with pytest.raises(ValueError, match="too few"):
        align([slide(0, 0)], FakePdf([PAGES[0][:1]]))


def test_llm_boxes_become_page_points() -> None:
    # [ymin, xmin, ymax, xmax] on 0-1000, clamped to the page.
    assert to_page_box([100, 500, 500, 1010], 800.0, 600.0) == (400.0, 60.0, 800.0, 300.0)
    assert to_page_box([100, 500, 101, 900], 800.0, 600.0) is None


def test_a_region_counts_as_shown_when_the_frame_has_its_ink() -> None:
    expected = np.zeros((100, 100), dtype=bool)
    expected[20:40, 20:60] = True
    shown = expected.copy()
    half = expected.copy()
    half[20:40, 40:60] = False

    box = (10.0, 10.0, 70.0, 50.0)
    assert visible_share(shown, expected, box) == 1.0
    assert visible_share(half, expected, box) == 0.5
    assert visible_share(shown, np.zeros_like(expected), box) == 0.0
    assert clip((-5.0, 5.0, 500.0, 50.0), (0, 0, 480, 360)) == (0.0, 5.0, 480.0, 50.0)


def test_each_page_gives_a_few_frames_spread_out() -> None:
    quota = Quota(per_page=2, gap_s=10.0)

    taken = [quota.take(page, t) for page, t in [(3, 0), (3, 4), (3, 12), (3, 30), (5, 31)]]

    assert taken == [True, False, True, False, True]


def test_the_dataset_is_split_by_lecture_and_written_as_coco(tmp_path: Path) -> None:
    def entry(name: str, split: str) -> dataset.LectureEntry:
        return dataset.LectureEntry(
            name=name, video="v.mp4", video_sha256="", pdf="p.pdf", pdf_sha256="", split=split
        )

    train, test = entry("a", "train"), entry("b", "test")
    image = Image.new("RGB", (480, 360), "white")

    def frame(time_s: float) -> LabelledFrame:
        return LabelledFrame(
            time_s,
            "slide",
            image,
            [("slide", (60.0, 0.0, 420.0, 350.0)), ("figure", (100.0, 100.0, 150.0, 140.0))],
        )

    counts = dataset.write(
        [(train, 100.0, frame(10.0)), (train, 100.0, frame(90.0)), (test, 50.0, frame(10.0))],
        tmp_path,
    )

    assert {split: c["images"] for split, c in counts.items()} == {
        "train": 1,
        "valid": 1,
        "test": 1,
    }
    coco = json.loads((tmp_path / "train" / "_annotations.coco.json").read_text())
    assert [c["name"] for c in coco["categories"]] == [
        "slide",
        "person",
        "figure",
        "annotation",
    ]
    assert coco["annotations"][1] == {
        "id": 2,
        "image_id": 1,
        "category_id": 3,
        "bbox": [100.0, 100.0, 50.0, 40.0],
        "area": 2000.0,
        "iscrowd": 0,
    }
    assert (tmp_path / "train" / coco["images"][0]["file_name"]).is_file()
