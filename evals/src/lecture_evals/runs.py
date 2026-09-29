"""What an eval run produces, where it's kept, and the gate.

Every suite returns a SuiteResult: flat metrics plus what they were measured on. The CLI saves
each one as an `eval_runs` row (suite, dataset hash, git commit, config, metrics) and checks the
metrics against evals/thresholds.json, which is the CI eval gate's contract.
"""

import asyncio
import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.models import EvalRun
from lecture_core.settings import Settings


class SuiteResult(BaseModel):
    suite: str
    dataset: str
    dataset_sha256: str
    lecture_id: uuid.UUID | None
    config: dict[str, Any]
    metrics: dict[str, float]
    report: str | None = None


class Bound(BaseModel):
    min: float | None = None
    max: float | None = None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_thresholds(path: Path) -> dict[str, dict[str, Bound]]:
    raw: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return {
        suite: {metric: Bound.model_validate(bound) for metric, bound in metrics.items()}
        for suite, metrics in raw.items()
        if not suite.startswith("_")
    }


def failures(result: SuiteResult, thresholds: dict[str, dict[str, Bound]]) -> list[str] | None:
    """The metrics outside their bounds; None if the suite has no thresholds."""
    bounds = thresholds.get(result.suite)
    if bounds is None:
        return None
    failed = []
    for metric, bound in bounds.items():
        value = result.metrics.get(metric)
        if value is None:
            failed.append(f"{metric}: missing")
        elif bound.min is not None and value < bound.min:
            failed.append(f"{metric}: {value:.3f} < {bound.min}")
        elif bound.max is not None and value > bound.max:
            failed.append(f"{metric}: {value:.3f} > {bound.max}")
    return failed


def git_sha(repo: Path = Path()) -> str | None:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{sha}-dirty" if dirty else sha


def save(results: list[tuple[SuiteResult, bool | None]], settings: Settings) -> None:
    """Record the runs in `eval_runs`."""

    async def insert() -> None:
        engine = create_engine(settings)
        sha = git_sha()
        async with create_sessionmaker(engine)() as session, session.begin():
            session.add_all(
                EvalRun(
                    suite=result.suite,
                    dataset=result.dataset,
                    dataset_sha256=result.dataset_sha256,
                    lecture_id=result.lecture_id,
                    git_sha=sha,
                    config=result.config,
                    metrics=result.metrics,
                    passed=passed,
                )
                for result, passed in results
            )
        await engine.dispose()

    asyncio.run(insert())
