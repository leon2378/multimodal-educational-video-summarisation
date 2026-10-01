# 0011: Lectures from any link, downloaded by a guarded yt-dlp

- Status: Accepted
- Date: 2026-10-02
- Code: `packages/core/src/lecture_core/links.py`, `packages/perception/src/lecture_perception/fetch.py`,
  the `fetch_source` activity, `POST /v1/lectures/from-url`

## Context

The blueprint (section 1) lists ingesting a lecture from a URL "from an openly licensed source".
Lectures often live on YouTube, in Zoom recordings or behind a direct link, and asking people
to download a video only to upload it again is friction, so links from anywhere are wanted, not
an allowlist of sites. That raises four problems:

- **The server fetches what a user names.** A link to `http://169.254.169.254/` would make the
  worker ask the cloud VM's metadata server for its credentials; `http://qdrant:6333/` would
  read the search index, private lectures included. The downloaded file is the lecture's
  video, which its owner can download back, so whatever a request returned would leak.
- **Sites.** Only a direct link is a plain HTTP download. YouTube, Vimeo, Zoom and most others
  need a site-specific client, and YouTube's changes so often that only a maintained one keeps
  up.
- **YouTube's terms** forbid downloading other than through YouTube, and copyright stays with the
  video's owner. YouTube also refuses many requests from cloud servers ("Sign in to confirm
  you're not a bot").
- **Size and time**: a link can name an 8-hour stream or a 20 GB file.

## Options

1. **Direct links only, fetched with httpx.** Simple to guard, but none of the sites people use.
2. **An allowlist of sites** (archive.org, MIT OpenCourseWare). Safest, but not what's wanted.
3. **Any link, through yt-dlp**, which knows over a thousand sites and plain files, with the
   download isolated from everything inside the stack.

For the isolation, a network egress proxy (Smokescreen, Squid) that refuses private addresses is
the strongest, but it's another service to run. yt-dlp's HTTP clients all go through Python's
sockets, so checking each connection inside the downloader's own process covers the same ground.

## Decision

Option 3.

- `POST /v1/lectures/from-url` makes the lecture and starts processing at once. Its first stage,
  `fetch`, downloads the video to where an upload would be (and is skipped on reprocessing,
  since it's there); then the pipeline runs as for an upload. A link counts as one of the day's
  uploads, with the same size limit (ADR 0009).
- **The link check** (`lecture_core.links.check_url`, in the API and again in the worker):
  http or https, no user name or password, no IP address off the public internet, and no name
  that only means something on a private network (a single label, `.internal`, `.local`).
- **The downloader runs in a process of its own** (`python -m lecture_perception.fetch`):
  - Its environment holds none of the worker's keys and tokens.
  - Every connection it opens is checked on the address it actually connects to, so a redirect
    or a DNS answer pointing inside is refused like a link would be.
  - yt-dlp's JavaScript runtime for YouTube (Deno) runs without network access.
  - yt-dlp's `curl-cffi` extra isn't installed: its own networking would get round the check.
- **Limits**: the byte limit is enforced three ways: yt-dlp's `max_filesize`, `RLIMIT_FSIZE` on
  the process, and a size check after. Videos longer than 3 hours are refused, a download gets
  an hour, a playlist gives its first video, and a live stream is refused. Before the file is
  stored, it's probed like an upload (only MP4/MOV, Matroska and WebM, with the decoders lectures
  use), so a link to anything else fails without leaving it where the owner can read it.
- **One file with picture and sound**, up to 720p where there's a choice: merging separate
  streams needs FFmpeg's command line, which the images don't have. YouTube usually gives 360p
  that way, which the MIT lectures showed is enough to read slides.
- **YouTube**: downloading goes against YouTube's terms, and copyright stays with the owner. Both
  are on whoever gives the link, and the upload dialog should say so. When YouTube refuses the
  server, the lecture fails with a reason that says to upload the file instead. Two optional
  secrets, for YouTube links only, get round some refusals:
  - `YOUTUBE_COOKIES_B64`: a signed-in account's cookies, from a spare account, since Google may
    flag one used this way.
  - `YOUTUBE_PROXY`: a proxy, a residential one being refused least.

## Consequences

- The worker image grows by yt-dlp and Deno (about 100 MB); the API image doesn't, since the
  link check is in `lecture_core`.
- On the demo's VM, YouTube links fail some of the time without the optional settings; on a home
  connection they work. Zoom works for public share links, not passcode or sign-in ones.
- yt-dlp needs updating as sites change; dependencies are updated by hand here, so a failing
  site is the prompt.
- Tests serve videos from this machine, which the checks refuse: the API takes
  `ALLOW_PRIVATE_LINKS` (tests only), and the worker's downloader takes a switch that only code
  can set, so the check on each connection can't be turned off from the environment.
- The web app needs a "from a link" option in its upload dialog, with the YouTube note.
