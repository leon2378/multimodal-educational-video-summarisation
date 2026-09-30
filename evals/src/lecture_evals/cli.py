"""lecture-eval: score the running stack on the eval datasets, record the runs, and gate them.

    uv run lecture-eval                          # every suite
    uv run lecture-eval --suites retrieval,asr   # some of them
    uv run lecture-eval --gate                   # exit 1 if a metric is past its threshold
    uv run lecture-eval --prepare --gate         # on a fresh stack: fetch media, process, gate

Suites: retrieval (search), answers (Q&A, with an LLM judge), asr (word error rate against the
captions), notes (concept citations against the captions) and slides (slide text against the
slide PDF). Each prints a summary, writes a detailed report to data/evals/<suite>/, and is saved
as an `eval_runs` row unless --no-save.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
from pydantic_ai.exceptions import AgentRunError

from lecture_core.settings import Settings
from lecture_evals import prepare
from lecture_evals.golden import GoldenLecture, GoldenSet
from lecture_evals.runs import Bound, SuiteResult, failures, load_thresholds, save
from lecture_evals.suites import answers, asr, notes, retrieval, slides
from lecture_llm.models import LLMConfigError, make_model
from lecture_llm.settings import LLMSettings

SUITES = ("retrieval", "answers", "asr", "notes", "slides")
THRESHOLDS = Path("evals/thresholds.json")
DEFAULT_OUT = Path("data/evals")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lecture-eval", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--suites", default=",".join(SUITES), help="comma-separated")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--golden", type=Path, default=answers.DEFAULT_DATASET)
    parser.add_argument("--captions", type=Path, default=asr.DEFAULT_DATASET)
    parser.add_argument("--captions-dir", type=Path, default=asr.DEFAULT_CAPTIONS_DIR)
    parser.add_argument("--slides", type=Path, default=slides.DEFAULT_DATASET)
    parser.add_argument("--pdf-dir", type=Path, default=slides.DEFAULT_PDF_DIR)
    parser.add_argument(
        "--notes-file",
        type=Path,
        help="score this result.json's notes (Gemini baseline or make process) in the notes suite",
    )
    parser.add_argument(
        "--video-dir", type=Path, default=Path("data/lectures"), help="for --prepare"
    )
    parser.add_argument(
        "--modes",
        default=",".join(retrieval.MODES),
        help="search modes the retrieval suite scores; the others' thresholds aren't checked",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="first download the datasets' missing media and process their lecture (as CI does)",
    )
    parser.add_argument("--judge-model", help="provider:model for the answer judge (LLM_MODEL)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between questions")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report directory")
    parser.add_argument("--no-save", action="store_true", help="don't record eval_runs rows")
    parser.add_argument("--gate", action="store_true", help=f"exit 1 past {THRESHOLDS}")
    args = parser.parse_args(argv)
    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    if unknown := [s for s in suites if s not in SUITES]:
        parser.error(f"unknown suite(s): {', '.join(unknown)}")
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if unknown := [m for m in modes if m not in retrieval.MODES]:
        parser.error(f"unknown search mode(s): {', '.join(unknown)}")

    runners: dict[str, Callable[[httpx.Client], tuple[SuiteResult, str]]] = {
        "retrieval": lambda client: _with_text(
            retrieval.run(client, args.golden, args.out, modes), retrieval.to_markdown
        ),
        "asr": lambda client: _with_text(
            asr.run(client, args.captions, args.captions_dir, args.out), asr.to_markdown
        ),
        "notes": lambda client: _with_text(
            notes.run(client, args.captions, args.captions_dir, args.out, args.notes_file),
            notes.to_markdown,
        ),
        "slides": lambda client: _with_text(
            slides.run(client, args.slides, args.pdf_dir, args.out), slides.to_markdown
        ),
        "answers": lambda client: _with_text(
            answers.run(client, args.golden, _judge(args.judge_model), args.out, args.pause),
            answers.to_markdown,
        ),
    }
    thresholds = load_thresholds(THRESHOLDS)
    unmeasured = _unmeasured(thresholds, modes)
    results: list[tuple[SuiteResult, bool | None]] = []
    gate_failed = False
    with httpx.Client(base_url=args.api_url, timeout=300) as client:
        if args.prepare:
            try:
                _prepare(client, suites, args)
            except (httpx.HTTPError, LookupError, RuntimeError, TimeoutError) as error:
                print(f"Couldn't prepare: {error}", file=sys.stderr)
                return 1
        for suite in suites:
            print(f"\n## {suite}\n")
            try:
                result, text = runners[suite](client)
            except (httpx.HTTPError, LookupError, LLMConfigError, AgentRunError) as error:
                # A suite that can't run (the API down, an LLM quota spent) fails the gate, and
                # the other suites still run. On stdout, so CI's job summary shows it.
                print(f"Couldn't run: {error}")
                gate_failed = True
                continue
            failed = failures(result, thresholds)
            print(text)
            if suite == "retrieval" and unmeasured:
                print(f"\nNot gated, as their modes weren't run: {', '.join(unmeasured)}")
            print(f"\nReport: {result.report}")
            if failed:
                print("Below threshold: " + "; ".join(failed))
                gate_failed = True
            elif failed is not None:
                print("Within thresholds.")
            results.append((result, None if failed is None else not failed))

    if results and not args.no_save:
        save(results, Settings())
    return 1 if args.gate and gate_failed else 0


def _unmeasured(thresholds: dict[str, dict[str, Bound]], modes: Sequence[str]) -> list[str]:
    """Takes the retrieval bounds for modes not run (named <mode>.<metric>) out of the gate."""
    bounds = thresholds.get("retrieval", {})
    unmeasured = [metric for metric in bounds if metric.split(".")[0] not in modes]
    for metric in unmeasured:
        del bounds[metric]
    return unmeasured


def _prepare(client: httpx.Client, suites: Sequence[str], args: argparse.Namespace) -> None:
    """The chosen suites' media, downloaded where missing, then their lectures processed."""
    lectures: dict[str, GoldenLecture] = {}
    files: list[prepare.MediaFile] = []
    if {"retrieval", "answers"} & set(suites):
        golden = GoldenSet.load(args.golden)
        lectures[golden.lecture.video_sha256] = golden.lecture
    if {"asr", "notes"} & set(suites):
        captions = asr.CaptionsSet.load(args.captions)
        lectures[captions.lecture.video_sha256] = captions.lecture
        srt = captions.captions
        files.append(prepare.MediaFile(args.captions_dir / srt.file, srt.sha256, srt.url))
    if "slides" in suites:
        slides_set = slides.SlidesSet.load(args.slides)
        lectures[slides_set.lecture.video_sha256] = slides_set.lecture
        pdf = slides_set.pdf
        files.append(prepare.MediaFile(args.pdf_dir / pdf.file, pdf.sha256, pdf.url))
    for lecture in lectures.values():
        video = args.video_dir / lecture.video
        files.append(prepare.MediaFile(video, lecture.video_sha256, lecture.video_url))
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as downloads:
        prepare.fetch(files, downloads)
    for lecture in lectures.values():
        prepare.process(client, lecture, args.video_dir / lecture.video)


def _with_text[R](
    run: tuple[SuiteResult, R], render: Callable[[R], str]
) -> tuple[SuiteResult, str]:
    result, report = run
    return result, render(report)


def _judge(model: str | None) -> answers.Judge:
    settings = LLMSettings()
    if model:
        settings = settings.model_copy(update={"llm_model": model})
    return answers.Judge(make_model(settings))


if __name__ == "__main__":
    sys.exit(main())
