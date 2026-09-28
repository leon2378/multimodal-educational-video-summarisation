"""A tiny synthetic lecture for pipeline tests: slides, camera shots, a build and a revisit."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

WIDTH, HEIGHT = 320, 240


def slide(kind: str, build: bool = False) -> Image.Image:
    """White slides with black shapes standing in for text. "a" is a title and text lines,
    "b" a title and one large figure. A build adds a line to "a"."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 20, 290, 45), fill="black")
    if kind == "a":
        for y in (80, 110, 140):
            draw.rectangle((40, y, 260, y + 10), fill="black")
        if build:
            draw.rectangle((40, 170, 200, 180), fill="black")
    else:
        # An outlined chart with bars: mostly white, like a real figure slide.
        draw.rectangle((70, 80, 250, 210), outline="black", width=3)
        for x, top in ((100, 150), (150, 110), (200, 170)):
            draw.rectangle((x, top, x + 25, 207), fill="black")
    return image


def camera(seed: int = 0) -> Image.Image:
    """A dark lecture hall with a lighter figure in it."""
    rng = np.random.default_rng(seed)
    pixels = rng.integers(20, 60, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    pixels[60:220, 140:190] = 120
    return Image.fromarray(pixels)


# (start second, frame) for a 12-second lecture: camera, slide A (built up at 4 s),
# camera, slide B, then slide A again.
TIMELINE: list[tuple[float, Image.Image]] = [
    (0.0, camera(0)),
    (2.0, slide("a")),
    (4.0, slide("a", build=True)),
    (5.0, camera(1)),
    (6.0, slide("b")),
    (9.0, slide("a", build=True)),
]
DURATION_S = 12.0


def frames_at(fps: float = 1.0) -> list[tuple[float, Image.Image]]:
    """The TIMELINE sampled like media.sample_frames would, without encoding a video."""
    samples = []
    for i in range(int(DURATION_S * fps)):
        t = i / fps
        image = next(img for start, img in reversed(TIMELINE) if start <= t)
        samples.append((t, image))
    return samples


def write_video(path: Path, fps: int = 5) -> Path:
    """Encode TIMELINE as an MP4 with a 440 Hz tone, using codecs built into FFmpeg."""
    with av.open(str(path), mode="w") as container:
        video = container.add_stream("mpeg4", rate=fps)
        video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        audio = container.add_stream("aac", rate=48_000, layout="mono")
        for i, (_, image) in enumerate(frames_at(fps)):
            frame = av.VideoFrame.from_image(image)  # type: ignore[no-untyped-call]
            frame.pts, frame.time_base = i, Fraction(1, fps)
            container.mux(video.encode(frame))
        container.mux(video.encode(None))

        samples_per_frame, total = 1024, int(DURATION_S * 48_000)
        tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(total) / 48_000)).astype(np.float32)
        for start in range(0, total, samples_per_frame):
            chunk = tone[start : start + samples_per_frame].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="mono")
            frame.sample_rate, frame.pts = 48_000, start
            frame.time_base = Fraction(1, 48_000)
            container.mux(audio.encode(frame))
        container.mux(audio.encode(None))
    return path
