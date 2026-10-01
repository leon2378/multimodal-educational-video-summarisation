from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lecture_core.storage import ObjectStorage
from lecture_llm.agents import LectureLLM, Prompts
from lecture_pipeline.temporal.activities import PipelineActivities, Resources
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
