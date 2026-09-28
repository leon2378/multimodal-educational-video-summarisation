"""Speech recognition behind a Transcriber interface, so the model is a config choice.

faster-whisper is an optional extra (`lecture-perception[asr]`), imported only when a model
actually loads: the CPU worker and the tests never need it.
"""

import gc
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol

from lecture_core.timeline import Transcript, TranscriptSegment, Word

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


class Transcriber(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def device(self) -> str: ...

    def cache_params(self) -> dict[str, Any]:
        """Settings that can change the transcript, for the stage cache key."""
        ...

    def transcribe(
        self, audio: BinaryIO, on_progress: Callable[[float], None] | None = None
    ) -> Transcript: ...

    def close(self) -> None:
        """Release the model, e.g. to free VRAM for the next stage."""
        ...


@dataclass(frozen=True)
class WhisperConfig:
    # Local directory or a faster-whisper model name (downloaded on first use).
    model_path: str
    # What goes in the stage cache key: model plus pinned revision.
    model_id: str
    device: str = "auto"
    # "auto" is int8 weights with float16 compute on a GPU (about 1 GB of VRAM for
    # large-v3-turbo) and plain int8 on a CPU.
    compute_type: str = "auto"
    beam_size: int = 5
    batch_size: int = 8
    language: str | None = None
    vad: bool = True

    def resolved(self) -> "WhisperConfig":
        device = self.device
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        compute_type = self.compute_type
        if compute_type == "auto":
            compute_type = "int8_float16" if device == "cuda" else "int8"
        return WhisperConfig(
            self.model_path,
            self.model_id,
            device,
            compute_type,
            self.beam_size,
            self.batch_size,
            self.language,
            self.vad,
        )

    def cache_params(self) -> dict[str, Any]:
        """Settings that can change the transcript. The device only changes the speed."""
        return {
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "batch_size": self.batch_size,
            "language": self.language,
            "vad": self.vad,
        }


class FasterWhisperTranscriber:
    """Loads the model on first use and keeps it until `close()`, which frees the VRAM for the
    next stage on a 6 GB card."""

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config.resolved()
        self._model: WhisperModel | None = None

    @property
    def model_id(self) -> str:
        return self.config.model_id

    @property
    def device(self) -> str:
        return self.config.device

    def cache_params(self) -> dict[str, Any]:
        return self.config.cache_params()

    def transcribe(
        self, audio: BinaryIO, on_progress: Callable[[float], None] | None = None
    ) -> Transcript:
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        config = self.config
        if self._model is None:
            self._model = WhisperModel(
                config.model_path, device=config.device, compute_type=config.compute_type
            )
        segments, info = BatchedInferencePipeline(self._model).transcribe(
            audio,
            language=config.language,
            beam_size=config.beam_size,
            batch_size=config.batch_size,
            vad_filter=config.vad,
            word_timestamps=True,
        )
        transcript: list[TranscriptSegment] = []
        for segment in segments:
            transcript.append(
                TranscriptSegment(
                    start_s=segment.start,
                    end_s=segment.end,
                    text=segment.text.strip(),
                    words=[
                        Word(
                            start_s=word.start,
                            end_s=word.end,
                            text=word.word.strip(),
                            probability=word.probability,
                        )
                        for word in segment.words or []
                    ],
                )
            )
            if on_progress is not None and info.duration:
                on_progress(min(segment.end / info.duration, 1.0))
        return Transcript(language=info.language, duration_s=info.duration, segments=transcript)

    def close(self) -> None:
        self._model = None
        gc.collect()


def _cuda_available() -> bool:
    try:
        import ctranslate2
    except ImportError:
        return False
    return bool(ctranslate2.get_cuda_device_count())
