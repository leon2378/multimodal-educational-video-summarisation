"""ASR eval: the word error rate (WER) of a lecture's transcript against its human captions, how
many of the lecture's technical terms it gets, and the real-time factor of the recognition.

Both sides are normalised the same way (lecture_evals.captions). Term recall counts each term in
both texts: a term the captions say 5 times and the transcript 4 times scores 4/5. The real-time
factor is recognition time over audio length, from the lecture's first run that actually ran
speech recognition rather than reading it from the cache.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jiwer
from pydantic import BaseModel

from lecture_evals.captions import Cue, count, normalise, parse_srt
from lecture_evals.client import find_lecture, get
from lecture_evals.golden import GoldenLecture
from lecture_evals.runs import SuiteResult, file_sha256

DEFAULT_DATASET = Path("evals/datasets/captions/mit-6.0001-lecture-10.json")
DEFAULT_CAPTIONS_DIR = Path("data/lectures")


class CaptionsFile(BaseModel):
    file: str
    sha256: str
    source: str


class CaptionsSet(BaseModel):
    lecture: GoldenLecture
    captions: CaptionsFile
    terms: list[str]

    @classmethod
    def load(cls, path: Path) -> "CaptionsSet":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def read_captions(self, captions_dir: Path) -> list[Cue]:
        path = captions_dir / self.captions.file
        if not path.is_file():
            raise LookupError(f"missing {path}: {self.captions.source}")
        if file_sha256(path) != self.captions.sha256:
            raise LookupError(f"{path} isn't the captions this dataset was made with")
        return parse_srt(path.read_text(encoding="utf-8-sig"))


class TermCount(BaseModel):
    term: str
    reference: int
    hypothesis: int


class AsrReport(BaseModel):
    dataset: str
    api_url: str
    lecture_id: uuid.UUID
    started_at: datetime
    reference_words: int
    hypothesis_words: int
    wer: float
    substitutions: int
    deletions: int
    insertions: int
    term_recall: float
    terms: list[TermCount]
    asr_seconds: float | None
    audio_seconds: float | None
    real_time_factor: float | None


class TranscriptScore(BaseModel):
    reference_words: int
    hypothesis_words: int
    wer: float
    substitutions: int
    deletions: int
    insertions: int
    term_recall: float
    terms: list[TermCount]


def score(text: str, cues: list[Cue], terms: Sequence[str]) -> TranscriptScore:
    """A transcript's word error rate and term recall against the captions."""
    hypothesis = normalise(text)
    reference = [word for cue in cues for word in normalise(cue.text)]
    measures = jiwer.process_words(" ".join(reference), " ".join(hypothesis))
    counts = [
        TermCount(term=term, reference=count(term, reference), hypothesis=count(term, hypothesis))
        for term in terms
    ]
    said = sum(t.reference for t in counts)
    found = sum(min(t.reference, t.hypothesis) for t in counts)
    return TranscriptScore(
        reference_words=len(reference),
        hypothesis_words=len(hypothesis),
        wer=measures.wer,
        substitutions=measures.substitutions,
        deletions=measures.deletions,
        insertions=measures.insertions,
        term_recall=found / said if said else 1.0,
        terms=counts,
    )


def evaluate(
    client: httpx.Client,
    captions: CaptionsSet,
    cues: list[Cue],
    lecture_id: uuid.UUID | None = None,
    dataset: str = "",
) -> AsrReport:
    started_at = datetime.now(UTC)
    lecture_id = lecture_id or find_lecture(client, captions.lecture)
    lines: list[dict[str, Any]] = get(client, f"/v1/lectures/{lecture_id}/transcript")
    scored = score(" ".join(line["text"] for line in lines), cues, captions.terms)
    asr_seconds = _recognition_seconds(get(client, f"/v1/lectures/{lecture_id}/runs"))
    audio_seconds = get(client, f"/v1/lectures/{lecture_id}")["duration_s"]
    return AsrReport(
        dataset=dataset,
        api_url=str(client.base_url),
        lecture_id=lecture_id,
        started_at=started_at,
        **scored.model_dump(),
        asr_seconds=asr_seconds,
        audio_seconds=audio_seconds,
        real_time_factor=asr_seconds / audio_seconds if asr_seconds and audio_seconds else None,
    )


def _recognition_seconds(runs: list[dict[str, Any]]) -> float | None:
    """Speech recognition time in the oldest listed run that didn't read it from the cache."""
    for run in sorted(runs, key=lambda run: str(run["started_at"])):
        for stage in run["stages"]:
            if stage["stage"] == "asr" and not stage["cached"]:
                seconds: float = stage["seconds"]
                return seconds
    return None


def run(
    client: httpx.Client, dataset: Path, captions_dir: Path, out_dir: Path
) -> tuple[SuiteResult, AsrReport]:
    captions = CaptionsSet.load(dataset)
    report = evaluate(client, captions, captions.read_captions(captions_dir), dataset=str(dataset))
    path = _save(report, out_dir, dataset)
    metrics = {
        "wer": report.wer,
        "term_recall": report.term_recall,
        "hypothesis_to_reference_words": report.hypothesis_words / report.reference_words,
    }
    if report.real_time_factor is not None:
        metrics["real_time_factor"] = report.real_time_factor
    result = SuiteResult(
        suite="asr",
        dataset=str(dataset),
        dataset_sha256=file_sha256(dataset),
        lecture_id=report.lecture_id,
        config={"captions": captions.captions.file},
        metrics=metrics,
        report=str(path),
    )
    return result, report


def to_markdown(report: AsrReport) -> str:
    rtf = f"{report.real_time_factor:.3f}" if report.real_time_factor is not None else "unknown"
    missed = sorted(
        (t for t in report.terms if t.hypothesis < t.reference),
        key=lambda t: t.hypothesis - t.reference,
    )
    lines = [
        f"WER {report.wer:.1%} over {report.reference_words:,} caption words "
        f"({report.substitutions} substituted, {report.deletions} deleted, "
        f"{report.insertions} inserted); the transcript has {report.hypothesis_words:,} words.",
        f"Technical terms: {report.term_recall:.1%} recalled. Real-time factor: {rtf}.",
    ]
    if missed:
        lines.append(
            "Terms said more often than transcribed: "
            + ", ".join(f"{t.term} ({t.hypothesis}/{t.reference})" for t in missed)
        )
    return "\n".join(lines)


def _save(report: AsrReport, out_dir: Path, dataset: Path) -> Path:
    folder = out_dir / "asr"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dataset.stem}-{report.started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path
