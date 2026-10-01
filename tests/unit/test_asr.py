import io
from collections.abc import Iterator
from typing import Any

import pytest

from lecture_core.timeline import Transcript, TranscriptSegment
from lecture_perception.asr import FasterWhisperTranscriber, ModalTranscriber, WhisperConfig

CONFIG = WhisperConfig(model_path="data/models/whisper", model_id="whisper@abc", device="auto")


class FakeModal:
    """Stands in for the deployed function: progress, then the transcript."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.calls: list[tuple[bytes, str, dict[str, Any]]] = []

    def remote_gen(
        self, audio: bytes, model_id: str, params: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        self.calls.append((audio, model_id, params))
        yield from self.events


def test_modal_transcription_shares_the_local_gpu_workers_cache_key() -> None:
    local_gpu = FasterWhisperTranscriber(WhisperConfig("x", "whisper@abc", device="cuda"))
    remote = ModalTranscriber(CONFIG, function=FakeModal([]))

    assert remote.model_id == local_gpu.model_id
    assert remote.cache_params() == local_gpu.cache_params()
    assert remote.cache_params()["compute_type"] == "int8_float16"


def test_modal_transcription_reports_progress_and_returns_the_transcript() -> None:
    transcript = Transcript(
        language="en",
        duration_s=4.0,
        segments=[TranscriptSegment(start_s=0.0, end_s=4.0, text="Hello", words=[])],
    )
    fake = FakeModal(
        [
            {"progress": 0.0},
            {"progress": 0.5},
            {"transcript": transcript.model_dump(mode="json")},
        ]
    )
    progress: list[float] = []

    result = ModalTranscriber(CONFIG, function=fake).transcribe(
        io.BytesIO(b"flac"), progress.append
    )

    assert result == transcript
    assert progress == [0.0, 0.5]
    assert fake.calls == [(b"flac", "whisper@abc", ModalTranscriber(CONFIG).cache_params())]


def test_modal_transcription_without_a_transcript_fails() -> None:
    transcriber = ModalTranscriber(CONFIG, function=FakeModal([{"progress": 0.3}]))
    with pytest.raises(RuntimeError, match="without a transcript"):
        transcriber.transcribe(io.BytesIO(b"flac"))
