"""Lectures from a link: the video at a URL, downloaded with yt-dlp, which handles direct links
to a video file and the sites it knows (YouTube, Vimeo, Zoom share links, archive.org, ...).
docs/adr/0011-lectures-from-any-link.md has the reasoning.

Whoever gives the link chooses where the download connects, so it runs in a process of its own
(`python -m lecture_perception.fetch`), with none of the worker's secrets in its environment,
and every connection that process opens is checked against lecture_core.links.is_public on the
address it actually connects to: redirects and DNS rebinding get the same check. The file's
size is capped while it's written (RLIMIT_FSIZE, which the process sets on itself, on Linux)
and its length before the download starts; the caller then probes it like an upload.

yt-dlp takes one file with both picture and sound when the site has one, up to 720p where
there's a choice. YouTube serves the two apart, so then the picture and the sound come down one
after the other and media.join puts them in one file. yt-dlp can't join them itself: that needs
FFmpeg's command line, which the images don't have. YouTube blocks many cloud servers;
YOUTUBE_COOKIES and YOUTUBE_PROXY (lecture_pipeline.settings) are optional ways round that, and
without them a refusal says to upload the file instead.
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

# Up to 720p where there's a choice, in this order: one MP4 file with picture and sound; H.264
# and AAC apart, as YouTube has them, to join into MP4; one file of any kind; any picture and
# sound apart, to join into Matroska; the best there is. Sound apart is the original language's,
# which yt-dlp prefers to dubbed tracks. `?` lets through formats that don't say their height
# (direct links), and plain downloads come before streaming manifests.
FORMAT = "/".join(
    [
        "b[ext=mp4][height<=?720]",
        "bv[ext=mp4][vcodec^=avc1][height<=?720][protocol^=http]+ba[ext=m4a][protocol^=http]",
        "b[height<=?720]",
        "bv[height<=?720][protocol^=http]+ba[protocol^=http]",
        "bv[height<=?720]+ba",
        "b",
    ]
)
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
    (("requested format is not available",), "There's no video with sound at that link."),
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
    """Look the video up once, then download what FORMAT chose: one file, or the picture and
    the sound apart, each on its own and then joined."""
    import yt_dlp

    from lecture_perception import media

    log = _Log()
    options: dict[str, Any] = {
        "logger": log,
        "format": FORMAT,
        "paths": {"home": str(args.out), "temp": str(args.out)},
        "noplaylist": True,
        "playlist_items": "1",
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
            info = ydl.extract_info(args.url, download=False)
        if info and info.get("_type") == "playlist":
            info = next(iter(info.get("entries") or []), None)
        if not info:
            raise FetchError("That link has no video to download.")
        if reason := _refusal(info, args.max_duration):
            raise FetchError(reason)
        chosen: list[dict[str, Any]] = info.get("requested_formats") or [info]
        sizes = [one.get("filesize") or one.get("filesize_approx") for one in chosen]
        expected = sum(size for size in sizes if size) if all(sizes) else None
        if expected and expected > args.max_bytes:
            raise FetchError(_too_big(args.max_bytes))
        progress = _Progress(expected)
        # FORMAT joins at most one picture to one sound.
        names = ["video"] if len(chosen) == 1 else ["picture", "sound"]
        paths = [
            _download_format(
                yt_dlp, options, info, one["format_id"], name, args.max_bytes, progress, log
            )
            for one, name in zip(chosen, names, strict=True)
        ]
    except yt_dlp.utils.DownloadError as error:
        raise FetchError(_explain(str(error), args.max_bytes)) from error
    except OSError as error:  # EFBIG: a file reached RLIMIT_FSIZE
        raise FetchError(_too_big(args.max_bytes)) from error
    path = paths[0]
    if len(paths) == 2:
        try:
            path = media.join(paths[0], paths[1], args.out)
        except media.MediaError as error:
            raise FetchError(f"The video couldn't be downloaded: {error}") from error
        for part in paths:
            part.unlink()
    return {
        "path": str(path),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "license": info.get("license"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url"),
    }


def _download_format(
    yt_dlp: Any,
    options: dict[str, Any],
    info: dict[str, Any],
    format_id: str,
    name: str,
    max_bytes: int,
    progress: "_Progress",
    log: "_Log",
) -> Path:
    """One of the formats looked up, downloaded to `name`.ext, within what's left of the byte
    limit. It reuses the lookup: nothing is asked of the site again but the file itself."""
    left = max_bytes - progress.finished
    only = options | {
        "format": format_id,
        "outtmpl": {"default": f"{name}.%(ext)s"},
        "max_filesize": left,
        "progress_hooks": [progress.hook],
    }
    with yt_dlp.YoutubeDL(only) as ydl:
        # Without what the lookup chose (requested_formats and the rest), so this picks anew.
        done = ydl.process_ie_result(
            ydl.sanitize_info(info, remove_private_keys=True), download=True
        )
    downloads = (done or {}).get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
    if path is None or not path.is_file():
        # Skipped rather than failed: yt-dlp skips a file over max_filesize.
        if any("max-filesize" in line for line in log.lines):
            raise FetchError(_too_big(max_bytes))
        raise FetchError("That link has no video to download.")
    progress.finished += path.stat().st_size
    return path


def _refusal(info: dict[str, Any], max_duration: int) -> str | None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "That's a live stream: wait until its recording is up."
    if (duration := info.get("duration")) and duration > max_duration:
        allowed = _hours(max_duration)
        return f"The video is {_hours(duration)} long, more than the {allowed} allowed."
    return None


class _Progress:
    """The share downloaded, over every file, about once a second. Without the files' sizes
    up front, the share of the one downloading."""

    def __init__(self, expected: int | None) -> None:
        self.expected = expected
        self.finished = 0
        self._last = 0.0

    def hook(self, status: dict[str, Any]) -> None:
        if status.get("status") != "downloading" or time.monotonic() - self._last < 1:
            return
        self._last = time.monotonic()
        done = status.get("downloaded_bytes")
        share: float | None
        if self.expected:
            share = (self.finished + (done or 0)) / self.expected
        else:
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            share = done / total if total and done else None
        _emit({"progress": min(share, 1.0) if share is not None else None})


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
