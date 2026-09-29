"""Slides eval: how much of each slide's text the lecture's slide readings recover, against the
text of the lecturer's slide PDF.

Each slide shown in the video is matched to the PDF page it shares most words with, and scored
by word overlap: precision (the reading's words that are on the page), recall (the page's words
the reading has) and F1. Word overlap rather than character error rate, because the PDF and a
reading order a slide's text differently (side notes, code boxes, table cells), and order isn't
what search and the notes use. Pages the video never shows aren't counted.

The PDF is the slides as published, so words the lecturer wrote over them in class count
against precision; the readings from OCR and from the vision LLM are also scored apart.
"""

import re
import statistics
import unicodedata
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from pypdf import PdfReader

from lecture_evals.client import find_lecture, get
from lecture_evals.golden import GoldenLecture
from lecture_evals.runs import SuiteResult, file_sha256

DEFAULT_DATASET = Path("evals/datasets/slides/mit-6.0001-lecture-10.json")
DEFAULT_PDF_DIR = Path("data/lectures")


class SlidesPdf(BaseModel):
    file: str
    sha256: str
    source: str
    # Text every page carries (course name, page number), removed before scoring.
    footer: str = ""


class SlidesSet(BaseModel):
    lecture: GoldenLecture
    pdf: SlidesPdf

    @classmethod
    def load(cls, path: Path) -> "SlidesSet":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def read_pages(self, pdf_dir: Path) -> list[str]:
        path = pdf_dir / self.pdf.file
        if not path.is_file():
            raise LookupError(f"missing {path}: {self.pdf.source}")
        if file_sha256(path) != self.pdf.sha256:
            raise LookupError(f"{path} isn't the PDF this dataset was made with")
        pages = [page.extract_text() or "" for page in PdfReader(path).pages]
        if self.pdf.footer:
            footer = re.compile(self.pdf.footer)
            pages = [footer.sub(" ", page) for page in pages]
        return pages


def words(text: str) -> Counter[str]:
    """Lower-case words and numbers; ligatures and full-width forms folded (NFKC)."""
    return Counter(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()))


class Overlap(BaseModel):
    precision: float
    recall: float
    f1: float

    @classmethod
    def of(cls, reading: Counter[str], page: Counter[str]) -> "Overlap":
        common = sum((reading & page).values())
        precision = common / sum(reading.values()) if reading else 0.0
        recall = common / sum(page.values()) if page else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return cls(precision=precision, recall=recall, f1=f1)


class SlideScore(BaseModel):
    slide_id: int
    reader: str
    title: str
    # 1-based, as a PDF viewer numbers pages.
    page: int
    overlap: Overlap


class SlidesReport(BaseModel):
    dataset: str
    lecture_id: uuid.UUID
    started_at: datetime
    pages: int
    slides: list[SlideScore]

    def mean(self, field: str, reader: str | None = None) -> float | None:
        values = [
            getattr(s.overlap, field) for s in self.slides if reader is None or s.reader == reader
        ]
        return statistics.fmean(values) if values else None


def reading_text(slide: dict[str, Any]) -> str:
    """Everything a reading says the slide shows in words; not its description of figures."""
    return "\n".join([slide["title"], slide["text"], slide["code"], *slide["latex"]])


def score(slides: list[dict[str, Any]], pages: list[str]) -> list[SlideScore]:
    page_words = [words(page) for page in pages]
    scores = []
    for slide in slides:
        reading = words(reading_text(slide))
        overlaps = [Overlap.of(reading, page) for page in page_words]
        best = max(range(len(pages)), key=lambda i: overlaps[i].f1)
        scores.append(
            SlideScore(
                slide_id=slide["slide_id"],
                reader=slide.get("reader", "vlm"),
                title=slide["title"],
                page=best + 1,
                overlap=overlaps[best],
            )
        )
    return scores


def evaluate(
    client: httpx.Client,
    slides_set: SlidesSet,
    pages: list[str],
    lecture_id: uuid.UUID | None = None,
    dataset: str = "",
) -> SlidesReport:
    started_at = datetime.now(UTC)
    lecture_id = lecture_id or find_lecture(client, slides_set.lecture)
    slides: list[dict[str, Any]] = get(client, f"/v1/lectures/{lecture_id}/slides")
    return SlidesReport(
        dataset=dataset,
        lecture_id=lecture_id,
        started_at=started_at,
        pages=len(pages),
        slides=score(slides, pages),
    )


def run(
    client: httpx.Client, dataset: Path, pdf_dir: Path, out_dir: Path
) -> tuple[SuiteResult, SlidesReport]:
    slides_set = SlidesSet.load(dataset)
    report = evaluate(client, slides_set, slides_set.read_pages(pdf_dir), dataset=str(dataset))
    folder = out_dir / "slides"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dataset.stem}-{report.started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    total = len(report.slides)
    metrics = {
        "slides": float(total),
        "word_precision": report.mean("precision") or 0.0,
        "word_recall": report.mean("recall") or 0.0,
        "word_f1": report.mean("f1") or 0.0,
        "read_by_ocr": sum(s.reader == "ocr" for s in report.slides) / total if total else 0.0,
        "titled": sum(bool(s.title) for s in report.slides) / total if total else 0.0,
    }
    for reader in ("ocr", "vlm"):
        if (f1 := report.mean("f1", reader)) is not None:
            metrics[f"word_f1_{reader}"] = f1
    result = SuiteResult(
        suite="slides",
        dataset=str(dataset),
        dataset_sha256=file_sha256(dataset),
        lecture_id=report.lecture_id,
        config={"pdf": slides_set.pdf.file, "pages": report.pages},
        metrics=metrics,
        report=str(path),
    )
    return result, report


def to_markdown(report: SlidesReport) -> str:
    by_reader = Counter(s.reader for s in report.slides)
    lines = [
        f"{len(report.slides)} slides against {report.pages} PDF pages: word precision "
        f"{report.mean('precision') or 0:.2f}, recall {report.mean('recall') or 0:.2f}, "
        f"F1 {report.mean('f1') or 0:.2f}.",
        "By reader: "
        + ", ".join(
            f"{reader} {count} slides, F1 {report.mean('f1', reader) or 0:.2f}"
            for reader, count in sorted(by_reader.items())
        )
        + f"; {sum(not s.title for s in report.slides)} without a title.",
    ]
    weakest = sorted(report.slides, key=lambda s: s.overlap.f1)[:3]
    lines.append(
        "Weakest: "
        + "; ".join(
            f"slide {s.slide_id} ({s.reader}, page {s.page}) F1 {s.overlap.f1:.2f}" for s in weakest
        )
    )
    return "\n".join(lines)
