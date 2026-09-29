"""The eval suites against a mocked API: captions, WER, concept citations, answer metrics and
the gate."""

import hashlib
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from lecture_evals.captions import count, normalise, occurrences, parse_srt, timed_words
from lecture_evals.golden import GoldenLecture, GoldenQuestion
from lecture_evals.runs import Bound, SuiteResult, failures, load_thresholds
from lecture_evals.suites import answers, asr, notes, slides

REPO_ROOT = Path(__file__).resolve().parents[2]
LECTURE_ID = uuid.UUID("00000000-0000-4000-8000-00000000000b")
SRT = """1
00:00:00,000 --> 00:00:04,000
PROFESSOR: Big O notation
describes growth.

2
00:00:04,000 --> 00:00:08,000
Orders of growth [LAUGHTER] matter,
and it's linear.
"""
LECTURE = GoldenLecture(
    title="T",
    video="v.mp4",
    video_sha256="ab" * 32,
    duration="00:10",
    source="https://example.org",
    licence="CC BY-NC-SA 4.0",
    attribution="Someone",
)


def test_captions_are_parsed_and_normalised() -> None:
    cues = parse_srt(SRT)

    assert [(c.start_s, c.end_s) for c in cues] == [(0.0, 4.0), (4.0, 8.0)]
    assert normalise(cues[0].text) == ["big", "o", "notation", "describes", "growth"]
    assert normalise(cues[1].text) == ["orders", "of", "growth", "matter", "and", "it's", "linear"]


def test_terms_are_found_in_time_with_plurals_matched() -> None:
    words = timed_words(parse_srt(SRT))

    assert occurrences("big O", words) == [0.0]
    # "order of growth" matches "Orders of growth", said at the start of the second cue.
    assert occurrences("order of growth", words) == [4.0]
    assert occurrences("quadratic", words) == []
    assert count("growth", [w.word for w in words]) == 2


def _api(routes: dict[str, Any]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond))


def _captions(tmp_path: Path) -> tuple[asr.CaptionsSet, Path]:
    # Bytes, so Windows doesn't turn the line endings into CRLF and change the hash.
    (tmp_path / "t.srt").write_bytes(SRT.encode())
    sha = hashlib.sha256(SRT.encode()).hexdigest()
    captions = asr.CaptionsSet(
        lecture=LECTURE,
        captions=asr.CaptionsFile(file="t.srt", sha256=sha, source="test"),
        terms=["big O", "order of growth", "linear"],
    )
    return captions, tmp_path


def test_word_error_rate_terms_and_real_time_factor(tmp_path: Path) -> None:
    captions, folder = _captions(tmp_path)
    runs = [
        {
            "started_at": "2026-02-01T00:00:00Z",
            "stages": [{"stage": "asr", "seconds": 0.1, "cached": True}],
        },
        {
            "started_at": "2026-01-01T00:00:00Z",
            "stages": [{"stage": "asr", "seconds": 2.0, "cached": False}],
        },
    ]
    client = _api(
        {
            f"/v1/lectures/{LECTURE_ID}/transcript": [
                {"text": "Big O notation describes growth."},
                {"text": "Order of growth matters and it's linear."},
            ],
            f"/v1/lectures/{LECTURE_ID}/runs": runs,
            f"/v1/lectures/{LECTURE_ID}": {"duration_s": 10.0},
        }
    )

    report = asr.evaluate(client, captions, captions.read_captions(folder), LECTURE_ID)

    # One substitution ("orders" -> "order", "matter" -> "matters") in 12 words.
    assert (report.substitutions, report.deletions, report.insertions) == (2, 0, 0)
    assert report.wer == pytest.approx(2 / 12)
    assert report.term_recall == 1.0
    # The first run that actually recognised speech: 2 s for 10 s of audio.
    assert report.real_time_factor == pytest.approx(0.2)
    assert "WER 16.7%" in asr.to_markdown(report)


def test_captions_that_changed_are_refused(tmp_path: Path) -> None:
    captions, folder = _captions(tmp_path)
    (folder / "t.srt").write_bytes((SRT + "\n3\n00:00:08,000 --> 00:00:09,000\nMore.\n").encode())

    with pytest.raises(LookupError, match="isn't the captions"):
        captions.read_captions(folder)


def test_concepts_are_checked_against_where_the_term_is_said(tmp_path: Path) -> None:
    captions, folder = _captions(tmp_path)
    study = {
        "tldr": "",
        "chapters": [{"title": "All", "start_s": 0.0, "end_s": 10.0, "summary": ""}],
        "concepts": [
            {"term": "Big O notation", "definition": "", "at_s": 1.0},
            {"term": "Orders of growth", "definition": "", "at_s": 30.0},
            {"term": "Memoisation", "definition": "", "at_s": 2.0},
        ],
        "formulas": [],
        "quiz": [],
    }
    client = _api({f"/v1/lectures/{LECTURE_ID}/notes": {"notes": study, "model": "m"}})

    report = notes.evaluate(client, captions, folder, LECTURE_ID)

    assert [c.distance_s for c in report.concepts] == [1.0, 26.0, None]
    assert (report.near, report.checkable) == (1, 2)
    # The concept cited at 30 s is past the 10 s video.
    assert report.checks.out_of_range == 1
    assert "never said" in notes.to_markdown(report)


def _slide(slide_id: int, title: str, text: str, reader: str = "vlm") -> dict[str, Any]:
    return {
        "slide_id": slide_id,
        "title": title,
        "text": text,
        "code": "",
        "latex": [],
        "figure_description": "a plot",
        "reader": reader,
    }


def test_slide_readings_are_scored_against_the_pages_they_show() -> None:
    pages = [
        "Timing a program\nuse the time module",
        "Counting operations\nassume these steps take constant time",
    ]
    client = _api(
        {
            f"/v1/lectures/{LECTURE_ID}/slides": [
                # Page 2 exactly, as OCR read it; the order doesn't matter.
                _slide(0, "COUNTING OPERATIONS", "constant time\nassume these steps take", "ocr"),
                # Page 1, missing two words and adding one the lecturer wrote on the slide.
                _slide(1, "", "Timing a program use the module wow"),
            ]
        }
    )
    slides_set = slides.SlidesSet(
        lecture=LECTURE, pdf=slides.SlidesPdf(file="s.pdf", sha256="", source="test")
    )

    report = slides.evaluate(client, slides_set, pages, LECTURE_ID)

    first, second = report.slides
    assert (first.page, first.reader, first.overlap.f1) == (2, "ocr", 1.0)
    assert second.page == 1
    # 6 of its 7 words are on the page, and it has 6 of the page's 7.
    assert second.overlap.precision == pytest.approx(6 / 7)
    assert second.overlap.recall == pytest.approx(6 / 7)
    assert report.mean("f1", "ocr") == 1.0
    assert "1 without a title" in slides.to_markdown(report)


def test_a_missing_slide_pdf_says_where_to_get_it(tmp_path: Path) -> None:
    missing = slides.SlidesSet(
        lecture=LECTURE, pdf=slides.SlidesPdf(file="none.pdf", sha256="", source="get it here")
    )

    with pytest.raises(LookupError, match="get it here"):
        missing.read_pages(tmp_path)
    assert slides.words("Ef\ufb01cient O(n^2)") == {"efficient": 1, "o": 1, "n": 1, "2": 1}


def _check(
    kind: str,
    verdict: str | None,
    supported: bool = True,
    citations: Sequence[tuple[float, bool]] = (),
    on_target: bool | None = None,
) -> answers.AnswerCheck:
    # "declined" stands for a declined answer, graded correct when the lecture doesn't cover
    # the question and wrong when it does.
    declined = verdict == "declined"
    graded = ("correct" if kind == "unanswerable" else "wrong") if declined else verdict
    judgement = (
        answers.Judgement(
            declined=declined, verdict=graded, supported=supported, unsupported=[], reason="r"
        )
        if verdict
        else None
    )
    return answers.AnswerCheck(
        id="q",
        kind=kind,
        question="?",
        answer="a",
        error=None if verdict else "busy",
        citations=[
            answers.CitationSeen(label="[00:01]", at_s=at, valid=ok) for at, ok in citations
        ],
        on_target=on_target,
        judgement=judgement,
        first_token_ms=1000,
        total_ms=2000,
        tokens=100,
    )


def test_answer_metrics() -> None:
    report = answers.AnswersReport(
        dataset="d",
        api_url="http://api",
        lecture_id=LECTURE_ID,
        started_at="2026-01-01T00:00:00Z",
        answer_model="a",
        judge_model="j",
        results=[
            _check("fact", "correct", citations=[(1, True)], on_target=True),
            _check(
                "fact",
                "partly",
                supported=False,
                citations=[(2, True), (3, False)],
                on_target=False,
            ),
            _check("fact", "declined"),
            _check("fact", None),
            _check("unanswerable", "declined"),
            _check("unanswerable", "wrong", citations=[(4, True)]),
        ],
    )

    values = answers.metrics(report)

    assert values["correctness"] == pytest.approx(1.5 / 4)
    assert values["faithfulness"] == pytest.approx(2 / 3)
    assert values["false_declines"] == pytest.approx(1 / 4)
    assert values["declines_when_uncovered"] == pytest.approx(1 / 2)
    assert values["citations_valid"] == pytest.approx(3 / 4)
    assert values["citations_on_target"] == pytest.approx(1 / 2)
    assert values["errors"] == 1
    assert values["first_token_p95_ms"] == 1000


def test_citations_on_target_need_to_be_near_the_answer() -> None:
    question = GoldenQuestion.model_validate(
        {"id": "q", "kind": "fact", "question": "?", "answer": "a", "spans": [["01:00", "01:30"]]}
    )
    near = answers.CitationSeen(label="[01:39]", at_s=99, valid=True)
    far = answers.CitationSeen(label="[02:00]", at_s=120, valid=True)
    invalid = answers.CitationSeen(label="[01:10]", at_s=70, valid=False)

    assert answers.on_target(question, [near])
    assert not answers.on_target(question, [far, invalid])


def test_evidence_is_rebuilt_from_sources_and_escaped() -> None:
    sources = [{"start_s": 60.0, "end_s": 90.0, "slide_title": "Loops"}]
    transcript = [
        {"start_s": 50.0, "text": "Before."},
        {"start_s": 60.2, "text": "A <b>loop</b>."},
        {"start_s": 95.0, "text": "After."},
    ]

    evidence = answers.render_evidence(sources, transcript)

    assert '<passage time="01:00-01:30" slide="Loops">' in evidence
    assert "[01:00] A &lt;b&gt;loop&lt;/b&gt;." in evidence
    assert "Before" not in evidence
    assert "After" not in evidence


def test_the_gate() -> None:
    result = SuiteResult(
        suite="retrieval",
        dataset="d",
        dataset_sha256="x",
        lecture_id=None,
        config={},
        metrics={"a": 0.8, "b": 5.0},
    )
    bounds = {"retrieval": {"a": Bound(min=0.9), "b": Bound(max=10), "c": Bound(min=0)}}

    assert failures(result, bounds) == ["a: 0.800 < 0.9", "c: missing"]
    assert failures(result.model_copy(update={"suite": "asr"}), bounds) is None


def test_the_committed_thresholds_are_valid() -> None:
    thresholds = load_thresholds(REPO_ROOT / "evals" / "thresholds.json")
    assert thresholds
    raw = json.loads((REPO_ROOT / "evals" / "thresholds.json").read_text(encoding="utf-8"))
    assert all(suite.startswith("_") or suite in thresholds for suite in raw)
