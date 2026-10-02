"""Lecture lifecycle: create, direct upload or a link, confirm, move between courses, and
delete. Processing is in routes/processing.py.

Upload flow:
    1. POST /v1/lectures                         -> lecture row + presigned PUT URL
    2. client PUTs the file straight to storage  (the API never sees the bytes)
    3. POST /v1/lectures/{id}/complete-upload    -> API checks the object exists and its size

Or, to resume after an interruption, in parts (docs/adr/0012-resumable-uploads-in-parts.md):
step 2 becomes POST /v1/lectures/{id}/upload-parts for a URL per part, again to resume, and
complete-upload joins the parts first.

Or POST /v1/lectures/from-url: processing starts at once and downloads the video first.
"""

import uuid
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lecture_api import quotas
from lecture_api.access import can_change, can_read, lecture_or_404, own_lecture, readable
from lecture_api.auth import SignedInDep, Viewer, ViewerDep
from lecture_api.deps import SearcherDep, SessionDep, SettingsDep, StorageDep, get_temporal
from lecture_api.routes.processing import start_workflow
from lecture_api.schemas import (
    LectureCreate,
    LectureCreated,
    LectureFromUrl,
    LectureOut,
    LectureUpdate,
    MediaOut,
    PartsUpload,
    PartTarget,
    UploadPartsRequest,
    UploadTarget,
)
from lecture_core.links import LinkError, check_url
from lecture_core.models import (
    Course,
    Lecture,
    LectureStatus,
    PipelineRun,
    UsageKind,
    Visibility,
)
from lecture_core.processing import workflow_id
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage, PartPlan, UploadedPart, source_key

router = APIRouter(prefix="/lectures", tags=["lectures"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_lecture(
    body: LectureCreate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    viewer: SignedInDep,
) -> LectureCreated:
    """A signed-in user's lecture is private to them. Without sign-in (local), it's public."""
    if body.size_bytes is not None:
        _check_size(body.size_bytes, quotas.upload_limit(viewer, settings))
    await quotas.check_upload(session, viewer, settings)
    if body.course_id is not None:
        await _joinable(session, body.course_id, viewer)
    lecture_id = uuid.uuid4()
    lecture = Lecture(
        id=lecture_id,
        title=body.title,
        course_id=body.course_id,
        owner_id=viewer.user_id,
        visibility=Visibility.PUBLIC if viewer.local else Visibility.PRIVATE,
        source_key=source_key(lecture_id, body.filename),
        source_filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        licence=body.licence,
        attribution=body.attribution,
    )
    session.add(lecture)
    quotas.record(session, UsageKind.UPLOAD, viewer.user_id)
    await session.commit()
    await session.refresh(lecture)

    # Presigning is local HMAC work, no network call, so it's fine on the event loop.
    url = storage.presign_put(lecture.source_key, body.content_type, settings.upload_url_ttl_s)
    return LectureCreated(
        lecture=LectureOut.model_validate(lecture),
        upload=UploadTarget(
            url=url,
            headers={"Content-Type": body.content_type},
            expires_in_s=settings.upload_url_ttl_s,
        ),
    )


@router.post("/from-url", status_code=status.HTTP_201_CREATED)
async def create_lecture_from_url(
    body: LectureFromUrl,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    viewer: SignedInDep,
) -> LectureOut:
    """A lecture from a link instead of an upload: a direct link to a video file, or a page on
    a site yt-dlp knows (YouTube, Vimeo, Zoom share links and many more). Processing starts at
    once and downloads it first, so follow its progress as for an upload. It counts as one of
    the day's uploads, with the same size limit. Links to addresses off the public internet are
    refused (docs/adr/0011-lectures-from-any-link.md). Downloading from YouTube goes against its
    terms, and copyright stays with the video's owner: both are on whoever gives the link."""
    try:
        url = check_url(body.url, settings.allow_private_links)
    except LinkError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    await quotas.check_upload(session, viewer, settings)
    if body.course_id is not None:
        await _joinable(session, body.course_id, viewer)
    # Connected only now, so a bad link or a spent quota is answered even while Temporal is
    # down, and no lecture is made if it is.
    temporal = await get_temporal(request)
    lecture_id = uuid.uuid4()
    lecture = Lecture(
        id=lecture_id,
        title=body.title or _title_from(url),
        course_id=body.course_id,
        owner_id=viewer.user_id,
        visibility=Visibility.PUBLIC if viewer.local else Visibility.PRIVATE,
        status=LectureStatus.PROCESSING,
        source_key=source_key(lecture_id, "video"),
        source_url=url,
        source_filename=_filename_from(url),
        # Until the download says what it is.
        content_type="video/mp4",
        licence=body.licence,
        attribution=body.attribution,
    )
    session.add(lecture)
    await session.flush()
    run = PipelineRun(lecture_id=lecture_id, workflow_id=workflow_id(lecture_id), stages=[])
    session.add(run)
    quotas.record(session, UsageKind.UPLOAD, viewer.user_id)
    await session.commit()
    await start_workflow(
        session,
        temporal,
        lecture,
        run,
        LectureStatus.FAILED,
        max_bytes=quotas.upload_limit(viewer, settings),
        title_from_source=body.title is None,
    )
    await session.refresh(lecture)
    return LectureOut.model_validate(lecture)


@router.get("")
async def list_lectures(
    session: SessionDep, viewer: ViewerDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[LectureOut]:
    """The lectures the caller may read: the public ones, and their own."""
    result = await session.scalars(
        select(Lecture)
        .where(readable(Lecture, viewer))
        .order_by(Lecture.created_at.desc())
        .limit(limit)
    )
    return [LectureOut.model_validate(lecture) for lecture in result]


@router.get("/{lecture_id}")
async def get_lecture(lecture_id: uuid.UUID, session: SessionDep, viewer: ViewerDep) -> LectureOut:
    return LectureOut.model_validate(await lecture_or_404(session, lecture_id, viewer))


@router.patch("/{lecture_id}")
async def update_lecture(
    lecture_id: uuid.UUID, body: LectureUpdate, session: SessionDep, viewer: SignedInDep
) -> LectureOut:
    """Change only the fields sent: the course (one of the caller's own), or, for admins, who
    can see the lecture."""
    lecture = await own_lecture(session, lecture_id, viewer)
    if "course_id" in body.model_fields_set:
        if body.course_id is not None:
            await _joinable(session, body.course_id, viewer)
        lecture.course_id = body.course_id
    if body.visibility is not None and body.visibility != lecture.visibility:
        if not viewer.admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins change who sees a lecture.")
        lecture.visibility = body.visibility
    await session.commit()
    await session.refresh(lecture)
    return LectureOut.model_validate(lecture)


@router.post("/{lecture_id}/upload-parts")
async def upload_parts(
    lecture_id: uuid.UUID,
    body: UploadPartsRequest,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    viewer: SignedInDep,
) -> PartsUpload:
    """Upload the video in parts, which can be resumed: the parts storage has, and a URL for
    each of the others. Ask again to resume after an interruption, or for new URLs when they
    expire. The first call starts the upload for a file of `size_bytes`, and later ones must
    give the same size. An upload left unfinished for a week is cleared, and resuming it then
    starts again (docs/adr/0012-resumable-uploads-in-parts.md)."""
    lecture = await own_lecture(session, lecture_id, viewer)
    if lecture.status != LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lecture is already {lecture.status}.")
    _check_size(body.size_bytes, quotas.upload_limit(viewer, settings))
    if lecture.upload_id is not None and body.size_bytes != lecture.size_bytes:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This upload is of a file of {lecture.size_bytes} bytes, not {body.size_bytes}. "
            "Choose the same file, or delete the lecture and add it again.",
        )

    upload_id = lecture.upload_id
    uploaded: list[UploadedPart] | None = None
    if upload_id is not None:
        uploaded = await run_in_threadpool(storage.uploaded_parts, lecture.source_key, upload_id)
    if upload_id is None or uploaded is None:
        # Not started yet, or cleared unfinished: from the first part.
        upload_id = await run_in_threadpool(
            storage.start_upload, lecture.source_key, lecture.content_type
        )
        lecture.upload_id, lecture.size_bytes = upload_id, body.size_bytes
        await session.commit()
        uploaded = []

    plan = PartPlan.for_size(body.size_bytes, settings.upload_part_bytes)
    done = plan.matching(uploaded)
    to_send = [number for number in range(1, plan.count + 1) if number not in done]
    # Signing is local HMAC work, but a few hundred URLs add up: off the event loop.
    parts = await run_in_threadpool(
        _sign_parts,
        storage,
        lecture.source_key,
        upload_id,
        plan,
        to_send,
        settings.upload_url_ttl_s,
    )
    return PartsUpload(
        size_bytes=plan.size_bytes,
        part_bytes=plan.part_bytes,
        count=plan.count,
        uploaded=sorted(done),
        parts=parts,
        expires_in_s=settings.upload_url_ttl_s,
    )


@router.post("/{lecture_id}/complete-upload")
async def complete_upload(
    lecture_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    viewer: SignedInDep,
) -> LectureOut:
    """Idempotent: confirming an upload that's already confirmed returns the lecture unchanged.
    An upload in parts is joined first, once storage has every part; until then it's a 409
    saying how many are missing."""
    lecture = await own_lecture(session, lecture_id, viewer)
    if lecture.status == LectureStatus.UPLOADED:
        return LectureOut.model_validate(lecture)
    if lecture.status != LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lecture is already {lecture.status}.")

    if lecture.upload_id is not None and lecture.size_bytes is not None:
        await _join_parts(
            storage, settings, lecture.source_key, lecture.upload_id, lecture.size_bytes
        )
    info = await run_in_threadpool(storage.head, lecture.source_key)
    if info is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No uploaded file found for this lecture.")
    limit = quotas.upload_limit(viewer, settings)
    if info.size > limit:
        await run_in_threadpool(storage.delete, lecture.source_key)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File is {info.size} bytes; the limit is {limit}.",
        )

    lecture.size_bytes = info.size
    lecture.upload_id = None
    lecture.status = LectureStatus.UPLOADED
    await session.commit()
    await session.refresh(lecture)
    return LectureOut.model_validate(lecture)


@router.get("/{lecture_id}/media")
async def media(
    lecture_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    viewer: ViewerDep,
) -> MediaOut:
    """A presigned URL for playing the uploaded video. Browsers play MP4 (H.264/AAC) directly;
    other formats will need the HLS renditions planned for later."""
    lecture = await lecture_or_404(session, lecture_id, viewer)
    if lecture.status == LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, "The video hasn't been uploaded yet.")
    return MediaOut(
        url=storage.presign_get(lecture.source_key, settings.upload_url_ttl_s),
        content_type=lecture.content_type,
        expires_in_s=settings.upload_url_ttl_s,
    )


@router.delete("/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lecture(
    lecture_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    searcher: SearcherDep,
    viewer: SignedInDep,
) -> None:
    """Delete the lecture everywhere: its video (or an unfinished upload's parts), its passages
    in the search index, its results, processing history and conversations. What processing
    computed stays in the stage cache, where another upload of the same video finds it
    (docs/adr/0001-stage-cache.md). Its owner or an admin only, and not while it's processing.
    What it used still counts towards today's quotas."""
    lecture = await own_lecture(session, lecture_id, viewer)
    if lecture.status == LectureStatus.PROCESSING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The lecture is being processed. Delete it once that's done."
        )
    # The search index and storage first: each step is safe to repeat, so after a failure the
    # lecture is still there to delete again.
    try:
        await run_in_threadpool(searcher.index.delete_lecture, lecture.id)
    except (httpx.HTTPError, ResponseHandlingException, UnexpectedResponse) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable right now. Try again soon."
        ) from error
    if lecture.upload_id is not None:
        await run_in_threadpool(storage.abort_upload, lecture.source_key, lecture.upload_id)
    await run_in_threadpool(storage.delete, lecture.source_key)
    await session.delete(lecture)
    await session.commit()


def _check_size(size: int, limit: int) -> None:
    if size > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File is {size} bytes; the limit is {limit}."
        )


def _sign_parts(
    storage: ObjectStorage,
    key: str,
    upload_id: str,
    plan: PartPlan,
    numbers: list[int],
    expires_in_s: int,
) -> list[PartTarget]:
    return [
        PartTarget(
            number=number,
            url=storage.presign_part(key, upload_id, number, plan.size_of(number), expires_in_s),
        )
        for number in numbers
    ]


async def _join_parts(
    storage: ObjectStorage, settings: Settings, key: str, upload_id: str, size_bytes: int
) -> None:
    """Join an upload's parts into the video, once storage has every one."""
    plan = PartPlan.for_size(size_bytes, settings.upload_part_bytes)
    uploaded = await run_in_threadpool(storage.uploaded_parts, key, upload_id)
    if uploaded is None:
        # Joined by a request just before this one, or cleared unfinished: whether the video
        # is there tells which.
        return
    parts = plan.matching(uploaded)
    missing = [number for number in range(1, plan.count + 1) if number not in parts]
    if len(missing) == 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Part {missing[0]} of {plan.count} hasn't been uploaded yet."
        )
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(missing)} of the {plan.count} parts haven't been uploaded yet, starting with "
            f"part {missing[0]}.",
        )
    ordered = [parts[number] for number in range(1, plan.count + 1)]
    await run_in_threadpool(storage.finish_upload, key, upload_id, ordered)


def _title_from(url: str) -> str:
    """A lecture's title until the video's own arrives: where it's from."""
    host = (urlsplit(url).hostname or "the web").removeprefix("www.")
    return f"Video from {host}"


def _filename_from(url: str) -> str:
    """The file the link names, if it names one; the download names it properly later."""
    name = PurePosixPath(urlsplit(url).path).name
    return name[:255] if "." in name else "video"


async def _joinable(session: AsyncSession, course_id: uuid.UUID, viewer: Viewer) -> None:
    """A course the lecture may join: one the caller may change. One they can't see fails
    validation, as a course that doesn't exist does."""
    course = await session.get(Course, course_id)
    if course is None or not can_read(course, viewer):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No course has that id.")
    if not can_change(course, viewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only its owner adds lectures to a course.")
