"""Courses: groups of lectures that search and Q&A can span.

POST   /v1/courses       create one (signed in; private to its creator unless an admin says public)
GET    /v1/courses       the courses the caller may read, with how many of their lectures they see
GET    /v1/courses/{id}  a course and the lectures in it the caller may read
DELETE /v1/courses/{id}  delete it: its lectures stay, outside any course, and its threads go

A lecture joins a course when it's created, or later with PATCH /v1/lectures/{id}. Search and
Q&A across a course cover whichever lectures are in it at the time: the search index is
filtered by lecture, so moving a lecture needs no re-indexing.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, func, select

from lecture_api.access import course_or_404, own_course, readable
from lecture_api.auth import SignedInDep, ViewerDep
from lecture_api.deps import SessionDep
from lecture_api.schemas import CourseCreate, CourseDetail, CourseOut, LectureOut
from lecture_core.models import Course, Lecture, Visibility

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(body: CourseCreate, session: SessionDep, viewer: SignedInDep) -> CourseOut:
    if body.visibility is not None and not viewer.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins choose who sees a course.")
    course = Course(
        title=body.title.strip(),
        description=body.description,
        owner_id=viewer.user_id,
        visibility=body.visibility or (Visibility.PUBLIC if viewer.local else Visibility.PRIVATE),
    )
    session.add(course)
    await session.commit()
    await session.refresh(course)
    return CourseOut.model_validate(course)


@router.get("")
async def list_courses(session: SessionDep, viewer: ViewerDep) -> list[CourseOut]:
    rows = await session.execute(
        select(Course, func.count(Lecture.id))
        .outerjoin(Lecture, and_(Lecture.course_id == Course.id, readable(Lecture, viewer)))
        .where(readable(Course, viewer))
        .group_by(Course.id)
        .order_by(Course.title)
    )
    return [
        CourseOut.model_validate(course).model_copy(update={"lecture_count": count})
        for course, count in rows
    ]


@router.get("/{course_id}")
async def get_course(course_id: uuid.UUID, session: SessionDep, viewer: ViewerDep) -> CourseDetail:
    course = await course_or_404(session, course_id, viewer)
    lectures = (
        await session.scalars(
            select(Lecture)
            .where(Lecture.course_id == course_id, readable(Lecture, viewer))
            .order_by(Lecture.created_at, Lecture.id)
        )
    ).all()
    return CourseDetail(
        **CourseOut.model_validate(course).model_dump(exclude={"lecture_count"}),
        lecture_count=len(lectures),
        lectures=[LectureOut.model_validate(lecture) for lecture in lectures],
    )


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: uuid.UUID, session: SessionDep, viewer: SignedInDep) -> None:
    await session.delete(await own_course(session, course_id, viewer))
    await session.commit()
