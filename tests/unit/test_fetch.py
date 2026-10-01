"""Lectures from a link: which links are refused, and downloads from a local web server, including
the downloader's own check on every connection (lecture_perception.fetch)."""

import asyncio
import functools
import http.server
import json
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from lecture_core.links import OFF_LIMITS, LinkError, check_url, is_public, is_youtube
from lecture_perception.fetch import Fetched, FetchError, download
from lecture_perception.media import MediaError, probe
from tests.unit import synthetic


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "http://archive.org/download/MIT6.0001F16/lecture.mp4",
        "https://8.8.8.8/video.mp4",
    ],
)
def test_links_to_websites_are_accepted(url: str) -> None:
    assert check_url(f"  {url} ") == url


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("ftp://example.com/v.mp4", "web link"),
        ("file:///etc/passwd", "web link"),
        ("javascript:alert(1)", "web link"),
        ("https://", "no website"),
        ("https://user:secret@example.com/v.mp4", "user name"),
        ("http://example.com:port/v.mp4", "valid"),
        ("https://example.com/" + "a" * 2048, "longer"),
        # Off the public internet: the machine itself, private networks, the cloud metadata
        # server, and names that only mean something on a private network.
        ("http://127.0.0.1/v.mp4", OFF_LIMITS),
        ("http://localhost:8000/v1/lectures", OFF_LIMITS),
        ("http://10.0.0.5/v.mp4", OFF_LIMITS),
        ("http://192.168.1.1/v.mp4", OFF_LIMITS),
        ("http://169.254.169.254/computeMetadata/v1/", OFF_LIMITS),
        ("http://metadata.google.internal/computeMetadata/v1/", OFF_LIMITS),
        ("http://[::1]:6333/collections", OFF_LIMITS),
        ("http://[::ffff:10.0.0.1]/v.mp4", OFF_LIMITS),
        ("http://qdrant:6333/collections", OFF_LIMITS),
        ("http://postgres.local/v.mp4", OFF_LIMITS),
    ],
)
def test_other_links_are_refused_with_a_reason(url: str, why: str) -> None:
    with pytest.raises(LinkError, match=re.escape(why)):
        check_url(url)


def test_public_addresses() -> None:
    assert is_public("8.8.8.8")
    assert is_public("2606:4700:4700::1111")
    for address in ("127.0.0.1", "10.1.2.3", "172.18.0.4", "100.64.0.1", "169.254.169.254"):
        assert not is_public(address)
    for address in ("::1", "fe80::1%eth0", "fd00::1", "::ffff:192.168.0.1", "224.0.0.1"):
        assert not is_public(address)


def test_youtube_links_are_recognised() -> None:
    assert is_youtube("https://www.youtube.com/watch?v=x")
    assert is_youtube("https://youtu.be/x")
    assert not is_youtube("https://notyoutube.com/x")
    assert not is_youtube("https://vimeo.com/1")


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A web server on this machine with the synthetic lecture and a text file."""
    root = tmp_path_factory.mktemp("site")
    synthetic.write_video(root / "lecture.mp4")
    (root / "notes.txt").write_text("Not a video.")
    handler = functools.partial(QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def _download(url: str, out: Path, max_bytes: int = 10**9) -> Fetched:
    # The site is on this machine: only tests lift the address check.
    return asyncio.run(
        download(
            url, out, max_bytes=max_bytes, max_duration_s=3600, timeout_s=120, allow_private=True
        )
    )


def test_a_direct_link_is_downloaded(site: str, tmp_path: Path) -> None:
    fetched = _download(f"{site}/lecture.mp4", tmp_path)

    assert (fetched.path.parent, fetched.ext, fetched.title) == (tmp_path, "mp4", "lecture")
    assert fetched.size_bytes == fetched.path.stat().st_size
    assert probe(fetched.path).duration_s == pytest.approx(synthetic.DURATION_S, abs=0.2)


def test_the_downloader_refuses_connections_off_the_public_internet(
    site: str, tmp_path: Path
) -> None:
    # The downloader process on its own, past the link check: its connection to this machine
    # is refused when it's made, as a redirect or a DNS answer pointing here would be.
    command = [sys.executable, "-m", "lecture_perception.fetch", "--out", str(tmp_path)]
    limits = ["--max-bytes", "1000000000", "--max-duration", "3600"]
    result = subprocess.run(  # noqa: S603 - the test's own command
        [*command, *limits, "--", f"{site}/lecture.mp4"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 1
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1] == {"error": OFF_LIMITS}
    assert not list(tmp_path.glob("video.*"))


def test_a_video_bigger_than_the_limit_is_refused(site: str, tmp_path: Path) -> None:
    with pytest.raises(FetchError, match=r"bigger than 0\.0 MB"):
        _download(f"{site}/lecture.mp4", tmp_path, max_bytes=10_000)


def test_a_link_to_something_else_fails_the_probe(site: str, tmp_path: Path) -> None:
    # Downloaded like any file, then turned away as an upload would be.
    fetched = _download(f"{site}/notes.txt", tmp_path)
    with pytest.raises(MediaError, match="isn't a readable video"):
        probe(fetched.path)


def test_a_bad_link_is_refused_before_downloading(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="isn't on the public internet"):
        asyncio.run(
            download(
                "http://169.254.169.254/computeMetadata/v1/",
                tmp_path,
                max_bytes=10**9,
                max_duration_s=3600,
                timeout_s=120,
            )
        )
