"""Reading lecture videos with PyAV. It bundles FFmpeg, so there's no ffmpeg to install."""

import heapq
import io
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import av
from av.container import InputContainer
from av.packet import Packet
from av.stream import Stream
from PIL import Image
from pydantic import BaseModel

# A video comes from whoever uploads it, and FFmpeg reads hundreds of formats, so a crafted file
# could reach a demuxer or decoder no lecture needs, where most of FFmpeg's vulnerabilities turn
# up (in 2026, the CineForm decoder and the subtitle parser that WTV files use). So FFmpeg opens
# only these containers and, while it works out what a file holds, starts only these decoders;
# a stream is decoded only if its decoder is one of them.
CONTAINERS = ("mov", "mp4", "matroska", "webm")
VIDEO_DECODERS = frozenset({"h264", "hevc", "vp8", "vp9", "libdav1d", "av1", "mpeg4"})
AUDIO_DECODERS = frozenset(
    {"aac", "mp3float", "mp3", "opus", "libopus", "vorbis", "flac", "alac", "ac3", "eac3"}
    | {"pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_f32le"}
)


class MediaError(ValueError):
    """The file isn't a usable lecture video. Retrying won't help."""


def _open(path: Path) -> InputContainer:
    try:
        return av.open(
            str(path),
            container_options={
                "format_whitelist": ",".join(CONTAINERS),
                "codec_whitelist": ",".join(sorted(VIDEO_DECODERS | AUDIO_DECODERS)),
            },
        )
    except av.error.FFmpegError as error:
        raise MediaError(
            f"{path.name} isn't a readable video (MP4, MOV, Matroska or WebM): {error}"
        ) from error


def _check_decoder(path: Path, stream: Stream, decoders: frozenset[str]) -> None:
    name = stream.codec_context.name
    if name not in decoders:
        raise MediaError(f"{path.name} has {stream.type} in {name}, which isn't supported")


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
        with _open(path) as container:
            if not container.streams.video:
                raise MediaError(f"{path.name} has no video stream")
            video = container.streams.video[0]
            _check_decoder(path, video, VIDEO_DECODERS)
            audio = container.streams.audio[0] if container.streams.audio else None
            if audio is not None:
                _check_decoder(path, audio, AUDIO_DECODERS)
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
    with _open(path) as source, av.open(buffer, mode="w", format="flac") as sink:
        if not source.streams.audio:
            raise MediaError(f"{path.name} has no audio stream")
        stream_in = source.streams.audio[0]
        _check_decoder(path, stream_in, AUDIO_DECODERS)
        stream_out = sink.add_stream("flac", rate=sample_rate, layout="mono", format="s16")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in source.decode(stream_in):
            for resampled in resampler.resample(frame):
                sink.mux(stream_out.encode(resampled))
        for resampled in resampler.resample(None):
            sink.mux(stream_out.encode(resampled))
        sink.mux(stream_out.encode(None))
    return buffer.getvalue()


def join(video_path: Path, audio_path: Path, out_dir: Path) -> Path:
    """One file with the picture from `video_path` and the sound from `audio_path`, which sites
    such as YouTube serve apart. The streams are copied, not re-encoded, into MP4 when it holds
    both codecs (with its index at the front, so playback starts before the whole file loads),
    or Matroska otherwise. The result is `video.mp4` or `video.mkv` in `out_dir`."""
    try:
        with _open(video_path) as video_in, _open(audio_path) as audio_in:
            if not video_in.streams.video:
                raise MediaError(f"{video_path.name} has no video stream")
            if not audio_in.streams.audio:
                raise MediaError(f"{audio_path.name} has no audio stream")
            video, audio = video_in.streams.video[0], audio_in.streams.audio[0]
            _check_decoder(video_path, video, VIDEO_DECODERS)
            _check_decoder(audio_path, audio, AUDIO_DECODERS)
            as_mp4 = (
                video.codec_context.name in _MP4_VIDEO and audio.codec_context.name in _MP4_AUDIO
            )
            out = out_dir / ("video.mp4" if as_mp4 else "video.mkv")
            with av.open(
                str(out),
                mode="w",
                format="mp4" if as_mp4 else "matroska",
                container_options={"movflags": "+faststart"} if as_mp4 else {},
            ) as sink:
                video_out = sink.add_stream_from_template(video)
                audio_out = sink.add_stream_from_template(audio)
                for packet in _in_time_order(video_in.demux(video), audio_in.demux(audio)):
                    packet.stream = video_out if packet.stream.type == "video" else audio_out
                    sink.mux(packet)
    except av.error.FFmpegError as error:
        raise MediaError(f"the picture and sound couldn't be joined: {error}") from error
    return out


# The codecs joined into MP4; any others go into Matroska, which holds them all.
_MP4_VIDEO = frozenset({"h264", "hevc", "mpeg4"})
_MP4_AUDIO = frozenset({"aac", "mp3float", "mp3"})


def _in_time_order(*sources: Iterator["Packet[Any]"]) -> Iterator["Packet[Any]"]:
    """Packets from several files, by decoding time, as a muxer takes them. Skips the empty
    packets demuxing ends with."""

    def timed(
        index: int, packets: Iterator["Packet[Any]"]
    ) -> Iterator[tuple[float, int, int, "Packet[Any]"]]:
        for order, packet in enumerate(packets):
            if packet.dts is not None and packet.time_base is not None:
                yield float(packet.dts * packet.time_base), index, order, packet

    for *_, packet in heapq.merge(*(timed(index, s) for index, s in enumerate(sources))):
        yield packet


def sample_frames(path: Path, fps: float = 1.0) -> Iterator[tuple[float, Image.Image]]:
    """Frames at roughly `fps` per second, as (seconds, image). Decodes every frame, since
    seeking per sample is slower than decoding straight through for low-resolution video."""
    step = 1 / fps
    with _open(path) as container:
        stream = container.streams.video[0]
        _check_decoder(path, stream, VIDEO_DECODERS)
        stream.thread_type = "AUTO"
        next_time = 0.0
        for frame in container.decode(stream):
            if frame.time is None or frame.time < next_time:
                continue
            next_time = (math.floor(frame.time / step) + 1) * step
            yield frame.time, frame.to_image()  # type: ignore[no-untyped-call]
