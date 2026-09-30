"""Who may see and change what (docs/adr/0009-clerk-sign-in-and-quotas.md).

Public lectures and courses (the demo) are readable by anyone. A signed-in user also reads and
changes their own, and their own conversations. Admins, and the local user when sign-in is off,
read and change everything. Something the viewer may not read answers 404, as if it didn't exist;
something they may read but not change answers 403.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, false, or_, true
from sqlalchemy.ext.asyncio import AsyncSession

from lecture_api.auth import Viewer
from lecture_core.models import Course, Lecture, QAThread, Visibility


def readable(model: type[Lecture] | type[Course], viewer: Viewer) -> ColumnElement[bool]:
    """A filter for the lectures or courses the viewer may read."""
    if viewer.admin:
        return true()
    if viewer.user is None:
        return model.visibility == Visibility.PUBLIC
    return or_(model.visibility == Visibility.PUBLIC, model.owner_id == viewer.user.id)


def can_read(item: Lecture | Course, viewer: Viewer) -> bool:
    return viewer.admin or item.visibility == Visibility.PUBLIC or _owns(item, viewer)


def can_change(item: Lecture | Course, viewer: Viewer) -> bool:
    return viewer.admin or _owns(item, viewer)


async def lecture_or_404(session: AsyncSession, lecture_id: uuid.UUID, viewer: Viewer) -> Lecture:
    lecture = await session.get(Lecture, lecture_id)
    if lecture is None or not can_read(lecture, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lecture not found.")
    return lecture


async def own_lecture(session: AsyncSession, lecture_id: uuid.UUID, viewer: Viewer) -> Lecture:
    """A lecture the viewer may change: their own, or any for an admin."""
    lecture = await lecture_or_404(session, lecture_id, viewer)
    if not can_change(lecture, viewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only its owner can change this lecture.")
    return lecture


async def course_or_404(session: AsyncSession, course_id: uuid.UUID, viewer: Viewer) -> Course:
    course = await session.get(Course, course_id)
    if course is None or not can_read(course, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")
    return course


async def own_course(session: AsyncSession, course_id: uuid.UUID, viewer: Viewer) -> Course:
    course = await course_or_404(session, course_id, viewer)
    if not can_change(course, viewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only its owner can change this course.")
    return course


def own_threads(viewer: Viewer) -> ColumnElement[bool]:
    """A filter for the conversations the viewer may see: their own, or all for an admin."""
    if viewer.admin:
        return true()
    if viewer.user is None:
        return false()
    return QAThread.user_id == viewer.user.id


async def thread_or_404(session: AsyncSession, thread_id: uuid.UUID, viewer: Viewer) -> QAThread:
    thread = await session.get(QAThread, thread_id)
    mine = viewer.user is not None and thread is not None and thread.user_id == viewer.user.id
    if thread is None or not (viewer.admin or mine):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
    return thread


def _owns(item: Lecture | Course, viewer: Viewer) -> bool:
    return viewer.user is not None and item.owner_id == viewer.user.id
