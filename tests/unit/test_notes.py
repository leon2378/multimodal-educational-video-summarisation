import pytest

from lecture_core.notes import format_timestamp, parse_timestamp


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("05:30", 330),
        ("5:30", 330),
        ("[12:07]", 727),
        ("1:02:03", 3723),
        ("75:10", 4510),  # minutes past 59 without an hour field
        ("00:09.5", 9.5),
    ],
)
def test_parse_timestamp(text: str, seconds: float) -> None:
    assert parse_timestamp(text) == seconds


@pytest.mark.parametrize("text", ["", "about 3 min", "330", "12:75", "1:75:00", "1:2:3:4"])
def test_parse_timestamp_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="not a timestamp"):
        parse_timestamp(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "00:00"), (59.9, "00:59"), (727, "12:07"), (3600, "1:00:00"), (3725, "1:02:05")],
)
def test_format_timestamp(seconds: float, text: str) -> None:
    assert format_timestamp(seconds) == text
