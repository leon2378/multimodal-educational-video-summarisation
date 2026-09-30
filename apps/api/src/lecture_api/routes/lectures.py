"""Lecture lifecycle: create, direct upload, confirm, and move between courses. Processing is in
routes/processing.py.

Upload flow:
    1. POST /v1/lectures                         -> lecture row + presigned PUT URL
    2. client PUTs the file straight to storage  (the API never sees the bytes)
    3. POST /v1/lectures/{id}/complete-upload    -> API checks the object exists and its size
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lecture_api import quotas
from lecture_api.access import can_change, can_read, lecture_or_404, own_lecture, readable
from lecture_api.auth import SignedInDep, Viewer, ViewerDep
from lecture_api.deps import SessionDep, SettingsDep, StorageDep
from lecture_api.schemas import (
    LectureCreate,
    LectureCreated,
    LectureOut,
    LectureUpdate,
    MediaOut,
    UploadTarget,
)
from lecture_core.models import Course, Lecture, LectureStatus, Visibility
from lecture_core.storage import source_key

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
        licence=body.licence,
        attribution=body.attribution,
    )
    session.add(lecture)
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


@router.post("/{lecture_id}/complete-upload")
async def complete_upload(
    lecture_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    viewer: SignedInDep,
) -> LectureOut:
    """Idempotent: confirming an upload that's already confirmed returns the lecture unchanged."""
    lecture = await own_lecture(session, lecture_id, viewer)
    if lecture.status == LectureStatus.UPLOADED:
        return LectureOut.model_validate(lecture)
    if lecture.status != LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lecture is already {lecture.status}.")

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


async def _joinable(session: AsyncSession, course_id: uuid.UUID, viewer: Viewer) -> None:
    """A course the lecture may join: one the caller may change. One they can't see fails
    validation, as a course that doesn't exist does."""
    course = await session.get(Course, course_id)
    if course is None or not can_read(course, viewer):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No course has that id.")
    if not can_change(course, viewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only its owner adds lectures to a course.")
