"""Lectures from a link: the video at a URL, downloaded with yt-dlp, which handles direct links
to a video file and the sites it knows (YouTube, Vimeo, Zoom share links, archive.org, ...).
docs/adr/0011-lectures-from-any-link.md has the reasoning.

Whoever gives the link chooses where the download connects, so it runs in a process of its own
(`python -m lecture_perception.fetch`), with none of the worker's secrets in its environment,
and every connection that process opens is checked against lecture_core.links.is_public on the
address it actually connects to: redirects and DNS rebinding get the same check. The file's
size is capped while it's written (RLIMIT_FSIZE, which the process sets on itself, on Linux)
and its length before the download starts; the caller then probes it like an upload.

yt-dlp takes a single file with both picture and sound, up to 720p where there's a choice:
merging separate streams needs FFmpeg's command line, which the images don't have. YouTube
blocks many cloud servers; YOUTUBE_COOKIES and YOUTUBE_PROXY (lecture_pipeline.settings) are
optional ways round that, and without them a refusal says to upload the file instead.
"""

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lecture_core.links import OFF_LIMITS, LinkError, check_url, is_public

logger = logging.getLogger(__name__)

# One file with picture and sound: MP4 at up to 720p if there's one, then anything up to 720p,
# then the best there is. `?` lets through formats that don't say their height (direct links).
FORMAT = "b[ext=mp4][height<=?720]/b[height<=?720]/b"
YOUTUBE_REFUSED = (
    "YouTube wouldn't let this server download the video. Download it on your own computer "
    "and upload the file instead."
)
_EXPLANATIONS = [
    (("sign in to confirm", "not a bot"), YOUTUBE_REFUSED),
    (("unsupported url",), "That link doesn't lead to a video that can be downloaded."),
    (
        ("private video", "video unavailable", "members-only", "this video is unavailable"),
        "The video isn't available to download: it may be private, removed or restricted.",
    ),
    (
        ("requested format is not available",),
        "The video has no single file with both picture and sound to download.",
    ),
    (("http error 404",), "Nothing was found at that link."),
    (("isn't on the public internet",), OFF_LIMITS),
]


class FetchError(ValueError):
    """The video at the link can't be downloaded. The message says why, for the person who gave
    it; retrying won't help."""


class Fetched(BaseModel):
    path: Path
    ext: str
    size_bytes: int
    # What the site says about the video, when it says.
    title: str | None = None
    duration_s: float | None = None
    licence: str | None = None
    uploader: str | None = None
    webpage_url: str | None = None


async def download(
    url: str,
    out_dir: Path,
    *,
    max_bytes: int,
    max_duration_s: float,
    timeout_s: float,
    progress: Callable[[float | None], None] = lambda _: None,
    cookies: str | None = None,
    proxy: str | None = None,
    allow_private: bool = False,
) -> Fetched:
    """The video at `url`, downloaded into `out_dir`. `progress` gets the share done (None when
    the size isn't known) about once a second. `cookies` (a cookies.txt) and `proxy` are for
    sites that refuse servers. `allow_private` lifts the address check, for tests only."""
    try:
        url = check_url(url, allow_private)
    except LinkError as error:
        raise FetchError(str(error)) from error
    args = [
        sys.executable,
        "-m",
        "lecture_perception.fetch",
        "--out",
        str(out_dir),
        "--max-bytes",
        str(max_bytes),
        "--max-duration",
        str(int(max_duration_s)),
    ]
    if cookies:
        cookie_file = await asyncio.to_thread(_write_cookies, out_dir, cookies)
        args += ["--cookies", str(cookie_file)]
    if allow_private:
        args.append("--allow-private")
    env = _clean_environment(out_dir)
    if proxy:
        env["FETCH_PROXY"] = proxy
    process = await asyncio.create_subprocess_exec(
        *args,
        "--",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr_stream = process.stdout, process.stderr
    if stdout is None or stderr_stream is None:
        raise RuntimeError("the downloader's output isn't connected")
    events: dict[str, Any] = {}

    async def read_events() -> None:
        async for line in stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if "progress" in event:
                progress(event["progress"])
            else:
                events.update(event)

    try:
        _, stderr, _ = await asyncio.wait_for(
            asyncio.gather(read_events(), stderr_stream.read(), process.wait()), timeout_s
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise FetchError(
            f"The download took longer than {timeout_s / 60:.0f} minutes, the most allowed."
        ) from None
    if "error" in events:
        raise FetchError(str(events["error"]))
    done = events.get("done")
    if process.returncode != 0 or not isinstance(done, dict):
        # Not the link's fault: the downloader itself failed. Worth a retry.
        tail = stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"the downloader stopped (exit {process.returncode}): {tail}")
    return await asyncio.to_thread(_fetched, done, out_dir, max_bytes)


def _fetched(done: dict[str, Any], out_dir: Path, max_bytes: int) -> Fetched:
    path = Path(done["path"])
    if path.parent.resolve() != out_dir.resolve() or not path.is_file():
        raise RuntimeError(f"the downloader wrote somewhere unexpected: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise FetchError(_too_big(max_bytes))
    return Fetched(
        path=path,
        ext=path.suffix.lstrip(".").lower(),
        size_bytes=size,
        title=done.get("title"),
        duration_s=done.get("duration"),
        licence=done.get("license"),
        uploader=done.get("uploader"),
        webpage_url=done.get("webpage_url"),
    )


def _write_cookies(out_dir: Path, cookies: str) -> Path:
    path = out_dir / "cookies.txt"
    path.write_text(cookies, encoding="utf-8")
    path.chmod(0o600)
    return path


def _clean_environment(home: Path) -> dict[str, str]:
    """What the downloader needs to run, and none of the worker's keys and tokens."""
    keep = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env |= {"HOME": str(home), "DENO_DIR": str(home / ".deno"), "PYTHONUTF8": "1"}
    return env


def _too_big(max_bytes: int) -> str:
    limit = f"{max_bytes / 1e9:.1f} GB" if max_bytes >= 1e9 else f"{max_bytes / 1e6:.1f} MB"
    return f"The video is bigger than {limit}, the most allowed."


# The downloader process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lecture_perception.fetch")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-duration", type=int, required=True)
    parser.add_argument("--cookies", type=Path)
    parser.add_argument("--allow-private", action="store_true")
    parser.add_argument("url")
    args = parser.parse_args(argv)
    if not args.allow_private:
        _guard_connections()
    _limit_file_size(args.max_bytes)
    try:
        _emit({"done": _download(args)})
    except FetchError as error:
        _emit({"error": str(error)})
        return 1
    return 0


def _guard_connections() -> None:
    """Refuse any connection from this process to an address off the public internet. Python's
    sockets are the only way out: yt-dlp's HTTP clients all use them, and its JavaScript runtime
    (for YouTube) runs without network access."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(address: Any) -> None:
        if not (isinstance(address, tuple) and is_public(str(address[0]))):
            raise ConnectionRefusedError(f"{address} isn't on the public internet")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        check(address)
        connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        check(address)
        return connect_ex(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign, assignment]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign, assignment]


def _limit_file_size(max_bytes: int) -> None:
    """A cap the kernel enforces on every file this process writes, with room for yt-dlp's
    small side files. Past it a write fails (Python ignores SIGXFSZ). Not on Windows."""
    if sys.platform != "win32":
        import resource

        limit = max_bytes + 16 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def _download(args: argparse.Namespace) -> dict[str, Any]:
    import yt_dlp

    refused: list[str] = []
    last_progress = 0.0

    def match(info: dict[str, Any], incomplete: bool = False) -> str | None:
        reason = None
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            reason = "That's a live stream: wait until its recording is up."
        elif (duration := info.get("duration")) and duration > args.max_duration:
            reason = (
                f"The video is {_hours(duration)} long, more than the {_hours(args.max_duration)}"
                " allowed."
            )
        if reason:
            refused.append(reason)
        return reason

    def hook(status: dict[str, Any]) -> None:
        nonlocal last_progress
        if status.get("status") != "downloading" or time.monotonic() - last_progress < 1:
            return
        last_progress = time.monotonic()
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes")
        _emit({"progress": min(done / total, 1.0) if total and done else None})

    log = _Log()
    options: dict[str, Any] = {
        "logger": log,
        "format": FORMAT,
        "outtmpl": {"default": "video.%(ext)s"},
        "paths": {"home": str(args.out), "temp": str(args.out)},
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": args.max_bytes,
        "match_filter": match,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "cachedir": False,
        "overwrites": True,
    }
    if args.cookies:
        options["cookiefile"] = str(args.cookies)
    if proxy := os.environ.get("FETCH_PROXY"):
        options["proxy"] = proxy
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(args.url, download=True)
    except yt_dlp.utils.DownloadError as error:
        raise FetchError(_explain(str(error), args.max_bytes)) from error
    except OSError as error:  # EFBIG: the file reached RLIMIT_FSIZE
        raise FetchError(_too_big(args.max_bytes)) from error
    if info and info.get("_type") == "playlist":
        info = next(iter(info.get("entries") or []), None)
    downloads = (info or {}).get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if info is None or not path or not Path(path).is_file():
        # Skipped rather than failed: by the filter above, or for being too big.
        if any("max-filesize" in line for line in log.lines):
            raise FetchError(_too_big(args.max_bytes))
        raise FetchError(refused[-1] if refused else "That link has no video to download.")
    return {
        "path": path,
        "title": info.get("title"),
        "duration": info.get("duration"),
        "license": info.get("license"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url"),
    }


class _Log:
    """yt-dlp's messages, kept to explain a skipped download, and kept off stdout, which
    carries this process's events. Warnings and errors go to stderr, for the worker's log."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, message: str) -> None:
        self.lines.append(message)

    def info(self, message: str) -> None:
        self.lines.append(message)

    def warning(self, message: str) -> None:
        self.lines.append(message)
        print(message, file=sys.stderr)

    def error(self, message: str) -> None:
        self.lines.append(message)
        print(message, file=sys.stderr)


def _explain(message: str, max_bytes: int) -> str:
    """yt-dlp's error, said for the person who gave the link."""
    lowered = message.lower()
    if "larger than max-filesize" in lowered or "file too large" in lowered:
        return _too_big(max_bytes)
    for needles, explanation in _EXPLANATIONS:
        if any(needle in lowered for needle in needles):
            return explanation
    detail = message.removeprefix("ERROR: ").strip()
    return f"The video couldn't be downloaded: {detail[:300]}"


def _hours(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


if __name__ == "__main__":
    sys.exit(main())
