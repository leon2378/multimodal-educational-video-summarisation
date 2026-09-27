import uuid

import pytest

from lecture_core.storage import source_key

LECTURE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    ("filename", "expected_suffix"),
    [
        ("lecture-01.mp4", ".mp4"),
        ("Lecture 1.MKV", ".mkv"),
        (r"C:\Users\me\Videos\talk.webm", ".webm"),
        ("no-extension", ""),
        ("weird.ext?x=1", ""),
    ],
)
def test_source_key(filename: str, expected_suffix: str) -> None:
    assert source_key(LECTURE_ID, filename) == f"raw/{LECTURE_ID}/source{expected_suffix}"
