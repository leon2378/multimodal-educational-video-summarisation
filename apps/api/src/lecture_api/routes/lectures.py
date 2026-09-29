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

from lecture_api.deps import SessionDep, SettingsDep, StorageDep, lecture_or_404
from lecture_api.schemas import (
    LectureCreate,
    LectureCreated,
    LectureOut,
    LectureUpdate,
    MediaOut,
    UploadTarget,
)
from lecture_core.models import Course, Lecture, LectureStatus
from lecture_core.storage import source_key

router = APIRouter(prefix="/lectures", tags=["lectures"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_lecture(
    body: LectureCreate, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> LectureCreated:
    await _check_course(session, body.course_id)
    lecture_id = uuid.uuid4()
    lecture = Lecture(
        id=lecture_id,
        title=body.title,
        course_id=body.course_id,
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
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[LectureOut]:
    result = await session.scalars(select(Lecture).order_by(Lecture.created_at.desc()).limit(limit))
    return [LectureOut.model_validate(lecture) for lecture in result]


@router.get("/{lecture_id}")
async def get_lecture(lecture_id: uuid.UUID, session: SessionDep) -> LectureOut:
    return LectureOut.model_validate(await lecture_or_404(session, lecture_id))


@router.patch("/{lecture_id}")
async def update_lecture(
    lecture_id: uuid.UUID, body: LectureUpdate, session: SessionDep
) -> LectureOut:
    """Change only the fields sent. So far that's the course."""
    lecture = await lecture_or_404(session, lecture_id)
    if "course_id" in body.model_fields_set:
        await _check_course(session, body.course_id)
        lecture.course_id = body.course_id
    await session.commit()
    await session.refresh(lecture)
    return LectureOut.model_validate(lecture)


@router.post("/{lecture_id}/complete-upload")
async def complete_upload(
    lecture_id: uuid.UUID, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> LectureOut:
    """Idempotent: confirming an upload that's already confirmed returns the lecture unchanged."""
    lecture = await lecture_or_404(session, lecture_id)
    if lecture.status == LectureStatus.UPLOADED:
        return LectureOut.model_validate(lecture)
    if lecture.status != LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lecture is already {lecture.status}.")

    info = await run_in_threadpool(storage.head, lecture.source_key)
    if info is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No uploaded file found for this lecture.")
    if info.size > settings.max_upload_bytes:
        await run_in_threadpool(storage.delete, lecture.source_key)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File is {info.size} bytes; the limit is {settings.max_upload_bytes}.",
        )

    lecture.size_bytes = info.size
    lecture.status = LectureStatus.UPLOADED
    await session.commit()
    await session.refresh(lecture)
    return LectureOut.model_validate(lecture)


@router.get("/{lecture_id}/media")
async def media(
    lecture_id: uuid.UUID, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> MediaOut:
    """A presigned URL for playing the uploaded video. Browsers play MP4 (H.264/AAC) directly;
    other formats will need the HLS renditions planned for later."""
    lecture = await lecture_or_404(session, lecture_id)
    if lecture.status == LectureStatus.AWAITING_UPLOAD:
        raise HTTPException(status.HTTP_409_CONFLICT, "The video hasn't been uploaded yet.")
    return MediaOut(
        url=storage.presign_get(lecture.source_key, settings.upload_url_ttl_s),
        content_type=lecture.content_type,
        expires_in_s=settings.upload_url_ttl_s,
    )


async def _check_course(session: AsyncSession, course_id: uuid.UUID | None) -> None:
    if course_id is not None and await session.get(Course, course_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No course has that id.")
