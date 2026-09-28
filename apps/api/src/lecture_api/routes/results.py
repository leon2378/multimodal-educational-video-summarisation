"""What processing produced: transcript, slides, timeline and study notes.

Each returns an empty list (or 404 for notes) until the lecture has been processed.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from lecture_api.deps import SessionDep, SettingsDep, StorageDep, lecture_or_404
from lecture_api.schemas import NotesOut, SlideOut, TimelineSegmentOut, TranscriptLineOut
from lecture_core.models import SlideRow, SummaryRow, TimelineSegmentRow, TranscriptSegmentRow
from lecture_core.notes import StudyNotes

router = APIRouter(prefix="/lectures", tags=["results"])


@router.get("/{lecture_id}/transcript")
async def transcript(lecture_id: uuid.UUID, session: SessionDep) -> list[TranscriptLineOut]:
    await lecture_or_404(session, lecture_id)
    rows = await session.scalars(
        select(TranscriptSegmentRow)
        .where(TranscriptSegmentRow.lecture_id == lecture_id)
        .order_by(TranscriptSegmentRow.index)
    )
    return [TranscriptLineOut.model_validate(row) for row in rows]


@router.get("/{lecture_id}/slides")
async def slides(
    lecture_id: uuid.UUID, session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> list[SlideOut]:
    """Image URLs are presigned, so the browser loads slides straight from storage."""
    await lecture_or_404(session, lecture_id)
    rows = await session.scalars(
        select(SlideRow).where(SlideRow.lecture_id == lecture_id).order_by(SlideRow.slide_id)
    )
    return [
        SlideOut(
            slide_id=row.slide_id,
            image_url=storage.presign_get(row.image_key, settings.upload_url_ttl_s),
            first_seen_s=row.first_seen_s,
            title=row.title,
            text=row.text,
            figure_description=row.figure_description,
            latex=row.latex,
            code=row.code,
            spans=row.spans,
        )
        for row in rows
    ]


@router.get("/{lecture_id}/timeline")
async def timeline(lecture_id: uuid.UUID, session: SessionDep) -> list[TimelineSegmentOut]:
    await lecture_or_404(session, lecture_id)
    rows = await session.scalars(
        select(TimelineSegmentRow)
        .where(TimelineSegmentRow.lecture_id == lecture_id)
        .order_by(TimelineSegmentRow.index)
    )
    return [TimelineSegmentOut.model_validate(row) for row in rows]


@router.get("/{lecture_id}/notes")
async def notes(lecture_id: uuid.UUID, session: SessionDep) -> NotesOut:
    await lecture_or_404(session, lecture_id)
    row = await session.get(SummaryRow, (lecture_id, "study_notes"))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No notes yet: process the lecture first.")
    return NotesOut(
        notes=StudyNotes.model_validate(row.content),
        model=row.model,
        run_id=row.run_id,
        created_at=row.created_at,
    )
