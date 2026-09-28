import io
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

from lecture_perception.media import MediaError, extract_audio, probe, sample_frames
from lecture_perception.slides import (
    DetectorConfig,
    detect_slides,
    difference_hash,
    distance,
    is_slide,
)
from tests.unit import synthetic


def gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"))


@pytest.fixture
def video(synthetic_video: Path) -> Path:
    return synthetic_video


def test_slides_and_camera_shots_are_told_apart() -> None:
    config = DetectorConfig()
    assert is_slide(gray(synthetic.slide("a")), config)
    assert is_slide(gray(synthetic.slide("b")), config)
    assert not is_slide(gray(synthetic.camera()), config)


def test_a_build_step_is_close_and_a_new_slide_is_far() -> None:
    a, built, b = (
        difference_hash(gray(image))
        for image in (synthetic.slide("a"), synthetic.slide("a", build=True), synthetic.slide("b"))
    )
    config = DetectorConfig()
    assert distance(a, built) < config.change_share < distance(a, b)


def test_detection_handles_camera_cuts_builds_and_revisits() -> None:
    detection = detect_slides(synthetic.frames_at(1.0), synthetic.DURATION_S)

    assert [(s.id, s.first_seen_s) for s in detection.slides] == [(0, 2.0), (1, 6.0)]
    # The camera shot at 5 s doesn't end slide 0; showing slide 0 again at 9 s reuses its id.
    assert [(s.slide_id, s.start_s, s.end_s) for s in detection.spans] == [
        (0, 2.0, 6.0),
        (1, 6.0, 9.0),
        (0, 9.0, 12.0),
    ]
    assert detection.slide_share == pytest.approx(9 / 12)
    # Slide 0 keeps its most complete frame: the one with the extra line.
    assert distance(
        difference_hash(gray(detection.slides[0].image)),
        difference_hash(gray(synthetic.slide("a", build=True))),
    ) < distance(
        difference_hash(gray(detection.slides[0].image)),
        difference_hash(gray(synthetic.slide("a"))),
    )


def test_a_slide_flashed_for_one_sample_is_ignored() -> None:
    frames = synthetic.frames_at(1.0)
    # Clicking through: slide B flashes up for one sample at 4 s, then it's back to slide A.
    frames[4] = (4.0, synthetic.slide("b"))
    frames[5] = (5.0, synthetic.slide("a", build=True))

    detection = detect_slides(frames, synthetic.DURATION_S)

    assert [(s.slide_id, s.start_s) for s in detection.spans] == [(0, 2.0), (1, 6.0), (0, 9.0)]


def test_probe_reads_the_video(video: Path) -> None:
    info = probe(video)
    assert info.duration_s == pytest.approx(synthetic.DURATION_S, abs=0.2)
    assert (info.width, info.height) == (synthetic.WIDTH, synthetic.HEIGHT)
    assert info.audio_codec == "aac"


def test_probe_rejects_a_file_that_isnt_video(tmp_path: Path) -> None:
    not_video = tmp_path / "notes.mp4"
    not_video.write_text("hello")
    with pytest.raises(MediaError, match="isn't a readable video"):
        probe(not_video)


def test_extract_audio_gives_16k_mono_flac(video: Path) -> None:
    with av.open(io.BytesIO(extract_audio(video)), mode="r") as container:
        stream = container.streams.audio[0]
        assert stream.codec_context.name == "flac"
        assert stream.codec_context.sample_rate == 16_000
        assert stream.codec_context.layout.name == "mono"
        assert container.duration is not None
        assert container.duration / av.time_base == pytest.approx(synthetic.DURATION_S, abs=0.2)


def test_sample_frames_at_one_per_second(video: Path) -> None:
    times = [t for t, _ in sample_frames(video, fps=1.0)]
    assert times == pytest.approx([float(s) for s in range(12)], abs=0.25)


def test_detection_works_on_the_encoded_video(video: Path) -> None:
    detection = detect_slides(sample_frames(video, 1.0), probe(video).duration_s)
    assert [s.slide_id for s in detection.spans] == [0, 1, 0]
