"""Lecture lifecycle. Phase 1 covers create, direct upload, and confirm; processing is Phase 2.

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

from lecture_api.deps import SessionDep, SettingsDep, StorageDep
from lecture_api.schemas import LectureCreate, LectureCreated, LectureOut, UploadTarget
from lecture_core.models import Lecture, LectureStatus
from lecture_core.storage import source_key

router = APIRouter(prefix="/lectures", tags=["lectures"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_lecture(
    body: LectureCreate, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> LectureCreated:
    lecture_id = uuid.uuid4()
    lecture = Lecture(
        id=lecture_id,
        title=body.title,
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
    return LectureOut.model_validate(await _get_or_404(session, lecture_id))


@router.post("/{lecture_id}/complete-upload")
async def complete_upload(
    lecture_id: uuid.UUID, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> LectureOut:
    """Idempotent: confirming an upload that's already confirmed returns the lecture unchanged."""
    lecture = await _get_or_404(session, lecture_id)
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


async def _get_or_404(session: AsyncSession, lecture_id: uuid.UUID) -> Lecture:
    lecture = await session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lecture not found.")
    return lecture
