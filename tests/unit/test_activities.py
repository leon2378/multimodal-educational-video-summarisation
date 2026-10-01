from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lecture_core.processing import StageInfo
from lecture_core.storage import ObjectStorage
from lecture_llm.agents import LectureLLM, Prompts, Usage
from lecture_pipeline.temporal.activities import PipelineActivities, Resources, spent_usd
from tests.unit.fakes import FakeLLM

PROMPTS = Path(__file__).resolve().parents[2] / "prompts" / "pipeline"


def test_each_activity_thread_has_its_own_llm_client(tmp_path: Path) -> None:
    """A client's pooled connections belong to the event loop that opened them, and each
    activity thread runs its own loop, so threads mustn't share one."""
    made: list[LectureLLM] = []

    def make_llm() -> LectureLLM:
        made.append(LectureLLM(FakeLLM().model, Prompts.load(PROMPTS)))
        return made[-1]

    storage = ObjectStorage.__new__(ObjectStorage)  # never used: no stage runs here
    activities = PipelineActivities(
        Resources(storage=storage, media_dir=tmp_path, make_llm=make_llm)
    )
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(lambda: (activities._llm(), activities._llm())).result()
        second = pool.submit(_in_new_thread, activities).result()

    assert first[0] is first[1]
    assert second is not first[0]
    assert len(made) == 2


def _in_new_thread(activities: PipelineActivities) -> LectureLLM:
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(activities._llm).result()


def test_a_run_is_charged_only_for_the_llm_stages_it_computed() -> None:
    # A million input tokens is $0.30 at flash-lite's paid-tier price.
    million = Usage(requests=1, input_tokens=1_000_000)
    by_stage = {"read_slides": million, "chapters": million, "draft_notes": million}

    def infos(*computed: str) -> list[StageInfo]:
        return [StageInfo(stage=s, seconds=1.0, cached=s not in computed) for s in by_stage]

    model = "google:gemini-3.5-flash-lite"
    assert spent_usd(model, by_stage, infos("chapters")) == pytest.approx(0.30)
    assert spent_usd(model, by_stage, infos("read_slides", "draft_notes")) == pytest.approx(0.60)
    # Everything from the cache: the usage the entries carry was paid for by an earlier run.
    assert spent_usd(model, by_stage, infos()) == 0.0
