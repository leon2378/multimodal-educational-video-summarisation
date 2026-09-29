"""Notes eval: is each concept in the study notes cited where the lecture says it, and do the
notes' timestamps hold up?

A concept's citation is compared with the nearest place the captions say its term (plurals
matched loosely). Some terms are never said as written, typically slide titles such as "Linear
search on sorted list": those can't be checked this way, so the share cited nearby is over the
checkable concepts, and the share that are checkable is reported beside it. The structural
checks are lecture_evals.checks: timestamps past the end of the video, chapters that overlap or
leave gaps, and how much of the video the chapters cover.

By default it scores the processed lecture's notes; `notes_file` scores a result.json instead,
from the Gemini baseline or `make process`, so the two can be compared the same way.
"""

import json
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, computed_field

from lecture_core.notes import StudyNotes, format_timestamp
from lecture_evals.captions import occurrences, timed_words
from lecture_evals.checks import NotesChecks, check_notes
from lecture_evals.client import find_lecture, get
from lecture_evals.runs import SuiteResult, file_sha256
from lecture_evals.suites.asr import CaptionsSet

DEFAULT_DATASET = Path("evals/datasets/captions/mit-6.0001-lecture-10.json")
NEAR_S = 10.0


class ConceptCheck(BaseModel):
    term: str
    cited_s: float
    # The nearest time the captions say the term; None if they never do.
    said_s: float | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def distance_s(self) -> float | None:
        return None if self.said_s is None else abs(self.cited_s - self.said_s)


class NotesReport(BaseModel):
    dataset: str
    # Where the notes came from: the lecture's id in the API, or a result.json.
    source: str
    lecture_id: uuid.UUID | None
    started_at: datetime
    model: str
    concepts: list[ConceptCheck]
    checks: NotesChecks

    @computed_field  # type: ignore[prop-decorator]
    @property
    def checkable(self) -> int:
        return sum(1 for c in self.concepts if c.said_s is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def near(self) -> int:
        return sum(1 for c in self.concepts if c.distance_s is not None and c.distance_s <= NEAR_S)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def median_distance_s(self) -> float | None:
        distances = [c.distance_s for c in self.concepts if c.distance_s is not None]
        return statistics.median(distances) if distances else None


def load_notes_file(path: Path) -> tuple[StudyNotes, str]:
    """The notes and model from a result.json written by the Gemini baseline or `make process`."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    run: dict[str, Any] = data.get("run", {})
    model = run.get("model") or run.get("llm_model") or path.parent.name
    return StudyNotes.model_validate(data["notes"]), str(model)


def evaluate(
    client: httpx.Client,
    captions: CaptionsSet,
    captions_dir: Path,
    lecture_id: uuid.UUID | None = None,
    dataset: str = "",
    notes_file: Path | None = None,
) -> NotesReport:
    started_at = datetime.now(UTC)
    if notes_file is not None:
        notes, model = load_notes_file(notes_file)
        source = str(notes_file)
    else:
        lecture_id = lecture_id or find_lecture(client, captions.lecture)
        stored: dict[str, Any] = get(client, f"/v1/lectures/{lecture_id}/notes")
        notes, model = StudyNotes.model_validate(stored["notes"]), stored["model"]
        source = f"{client.base_url}/v1/lectures/{lecture_id}/notes"
    words = timed_words(captions.read_captions(captions_dir))
    concepts = []
    for concept in notes.concepts:
        said = occurrences(concept.term, words)
        nearest = min(said, key=lambda at_s: abs(at_s - concept.at_s)) if said else None
        concepts.append(ConceptCheck(term=concept.term, cited_s=concept.at_s, said_s=nearest))
    return NotesReport(
        dataset=dataset,
        source=source,
        lecture_id=lecture_id,
        started_at=started_at,
        model=model,
        concepts=concepts,
        checks=check_notes(notes, captions.lecture.duration_s),
    )


def run(
    client: httpx.Client,
    dataset: Path,
    captions_dir: Path,
    out_dir: Path,
    notes_file: Path | None = None,
) -> tuple[SuiteResult, NotesReport]:
    report = evaluate(
        client, CaptionsSet.load(dataset), captions_dir, dataset=str(dataset), notes_file=notes_file
    )
    folder = out_dir / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dataset.stem}-{report.started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    total = len(report.concepts)
    metrics = {
        "concepts": float(total),
        "concepts_checkable": report.checkable / total if total else 0.0,
        "concepts_near": report.near / report.checkable if report.checkable else 0.0,
        "timestamps_out_of_range": float(report.checks.out_of_range),
        "chapter_issues": float(len(report.checks.issues)),
    }
    if report.median_distance_s is not None:
        metrics["concept_median_distance_s"] = report.median_distance_s
    if report.checks.chapter_coverage is not None:
        metrics["chapter_coverage"] = report.checks.chapter_coverage
    result = SuiteResult(
        suite="notes",
        dataset=str(dataset),
        dataset_sha256=file_sha256(dataset),
        lecture_id=report.lecture_id,
        config={"model": report.model, "source": report.source, "near_s": NEAR_S},
        metrics=metrics,
        report=str(path),
    )
    return result, report


def to_markdown(report: NotesReport) -> str:
    median = report.median_distance_s
    unsaid = len(report.concepts) - report.checkable
    lines = [
        f"{report.near} of {report.checkable} checkable concepts cited within {NEAR_S:.0f} s of "
        f"where the captions say the term"
        + (f" (median {median:.1f} s)" if median is not None else "")
        + (f"; {unsaid} more are never said as written" if unsaid else "")
        + f". Notes by {report.model}.",
        f"Checks: {report.checks.timestamps} timestamps, {report.checks.out_of_range} outside "
        f"the video, {len(report.checks.issues)} chapter issues.",
    ]
    far = [c for c in report.concepts if c.distance_s is None or c.distance_s > NEAR_S]
    if far:
        lines.append(
            "Off: "
            + "; ".join(
                f"{c.term} cited {format_timestamp(c.cited_s)}, "
                + (f"said {format_timestamp(c.said_s)}" if c.said_s is not None else "never said")
                for c in far
            )
        )
    return "\n".join(lines)
