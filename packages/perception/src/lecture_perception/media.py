"""Reading lecture videos with PyAV. It bundles FFmpeg, so there's no ffmpeg to install."""

import io
import math
from collections.abc import Iterator
from pathlib import Path

import av
from PIL import Image
from pydantic import BaseModel


class MediaError(ValueError):
    """The file isn't a usable lecture video. Retrying won't help."""


class MediaInfo(BaseModel):
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    audio_sample_rate: int | None


def probe(path: Path) -> MediaInfo:
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise MediaError(f"{path.name} has no video stream")
            video = container.streams.video[0]
            audio = container.streams.audio[0] if container.streams.audio else None
            if container.duration is not None:
                duration_s = container.duration / av.time_base
            elif video.duration is not None and video.time_base is not None:
                duration_s = float(video.duration * video.time_base)
            else:
                raise MediaError(f"{path.name} has no duration")
            return MediaInfo(
                duration_s=duration_s,
                width=video.codec_context.width,
                height=video.codec_context.height,
                fps=float(video.average_rate or 0),
                video_codec=video.codec_context.name,
                audio_codec=audio.codec_context.name if audio else None,
                audio_sample_rate=audio.codec_context.sample_rate if audio else None,
            )
    except av.error.FFmpegError as error:
        raise MediaError(f"{path.name} isn't a readable video: {error}") from error


def extract_audio(path: Path, sample_rate: int = 16_000) -> bytes:
    """Mono FLAC at `sample_rate`: what speech recognition models expect, at about a third of
    the size of the same audio as WAV."""
    buffer = io.BytesIO()
    with av.open(str(path)) as source, av.open(buffer, mode="w", format="flac") as sink:
        if not source.streams.audio:
            raise MediaError(f"{path.name} has no audio stream")
        stream_in = source.streams.audio[0]
        stream_out = sink.add_stream("flac", rate=sample_rate, layout="mono", format="s16")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in source.decode(stream_in):
            for resampled in resampler.resample(frame):
                sink.mux(stream_out.encode(resampled))
        for resampled in resampler.resample(None):
            sink.mux(stream_out.encode(resampled))
        sink.mux(stream_out.encode(None))
    return buffer.getvalue()


def sample_frames(path: Path, fps: float = 1.0) -> Iterator[tuple[float, Image.Image]]:
    """Frames at roughly `fps` per second, as (seconds, image). Decodes every frame, since
    seeking per sample is slower than decoding straight through for low-resolution video."""
    step = 1 / fps
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        next_time = 0.0
        for frame in container.decode(stream):
            if frame.time is None or frame.time < next_time:
                continue
            next_time = (math.floor(frame.time / step) + 1) * step
            yield frame.time, frame.to_image()  # type: ignore[no-untyped-call]
