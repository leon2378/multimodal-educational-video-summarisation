import json
from dataclasses import replace

import pytest
from pydantic import BaseModel

from lecture_pipeline.cache import StageCache, StageSpec, artifact_path, cache_key

ASR = StageSpec(
    name="asr",
    version="1",
    model="faster-whisper-large-v3-turbo-int8",
    params={"beam_size": 5, "vad": True},
)
INPUTS = {"audio": "sha256:3f1c"}


class Transcript(BaseModel):
    text: str


class InMemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data


def test_key_ignores_param_order() -> None:
    a = replace(ASR, params={"beam_size": 5, "vad": True})
    b = replace(ASR, params={"vad": True, "beam_size": 5})
    assert cache_key(a, INPUTS) == cache_key(b, INPUTS)


@pytest.mark.parametrize(
    "changed",
    [
        replace(ASR, name="asr-qwen"),
        replace(ASR, version="2"),
        replace(ASR, model="qwen3-asr-1.7b"),
        replace(ASR, params={"beam_size": 1, "vad": True}),
    ],
    ids=["name", "version", "model", "params"],
)
def test_key_changes_with_anything_that_affects_output(changed: StageSpec) -> None:
    assert cache_key(changed, INPUTS) != cache_key(ASR, INPUTS)


def test_key_changes_with_inputs() -> None:
    assert cache_key(ASR, {"audio": "sha256:other"}) != cache_key(ASR, INPUTS)


def test_run_computes_once_then_hits_cache() -> None:
    cache = StageCache(InMemoryStore())
    calls: list[str] = []

    def transcribe() -> Transcript:
        calls.append("called")
        return Transcript(text="today we cover dynamic programming")

    first = cache.run(ASR, INPUTS, Transcript, transcribe)
    second = cache.run(ASR, INPUTS, Transcript, transcribe)

    assert (first.cached, second.cached) == (False, True)
    assert second.output == first.output
    assert len(calls) == 1


def test_get_misses_before_run() -> None:
    assert StageCache(InMemoryStore()).get(ASR, INPUTS, Transcript) is None


def test_artifact_records_provenance() -> None:
    store = InMemoryStore()
    result = StageCache(store).run(ASR, INPUTS, Transcript, lambda: Transcript(text="hi"))

    stored = json.loads(store.objects[artifact_path("asr", result.key)])

    assert stored["key"] == result.key
    assert stored["model"] == ASR.model
    assert stored["params"] == dict(ASR.params)
    assert stored["inputs"] == INPUTS
    assert stored["output"] == {"text": "hi"}
