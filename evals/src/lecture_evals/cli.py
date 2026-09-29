"""lecture-eval: score the running stack on the eval datasets, record the runs, and gate them.

    uv run lecture-eval                          # every suite
    uv run lecture-eval --suites retrieval,asr   # some of them
    uv run lecture-eval --gate                   # exit 1 if a metric is past its threshold

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

from lecture_core.settings import Settings
from lecture_evals.runs import SuiteResult, failures, load_thresholds, save
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
    parser.add_argument("--judge-model", help="provider:model for the answer judge (LLM_MODEL)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between questions")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report directory")
    parser.add_argument("--no-save", action="store_true", help="don't record eval_runs rows")
    parser.add_argument("--gate", action="store_true", help=f"exit 1 past {THRESHOLDS}")
    args = parser.parse_args(argv)
    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    if unknown := [s for s in suites if s not in SUITES]:
        parser.error(f"unknown suite(s): {', '.join(unknown)}")

    runners: dict[str, Callable[[httpx.Client], tuple[SuiteResult, str]]] = {
        "retrieval": lambda client: _with_text(
            retrieval.run(client, args.golden, args.out), retrieval.to_markdown
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
    results: list[tuple[SuiteResult, bool | None]] = []
    gate_failed = False
    with httpx.Client(base_url=args.api_url, timeout=300) as client:
        for suite in suites:
            print(f"\n## {suite}\n")
            try:
                result, text = runners[suite](client)
            except (httpx.HTTPError, LookupError, LLMConfigError) as error:
                print(f"Couldn't run: {error}", file=sys.stderr)
                gate_failed = True
                continue
            failed = failures(result, thresholds)
            print(text)
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
