"""Courses: groups of lectures that search and Q&A can span.

POST   /v1/courses       create one
GET    /v1/courses       every course, with how many lectures it has
GET    /v1/courses/{id}  a course and its lectures
DELETE /v1/courses/{id}  delete it: its lectures stay, outside any course, and its threads go

A lecture joins a course when it's created, or later with PATCH /v1/lectures/{id}. Search and
Q&A across a course cover whichever lectures are in it at the time: the search index is
filtered by lecture, so moving a lecture needs no re-indexing.
"""

import uuid

from fastapi import APIRouter, status
from sqlalchemy import func, select

from lecture_api.deps import SessionDep, course_or_404
from lecture_api.schemas import CourseCreate, CourseDetail, CourseOut, LectureOut
from lecture_core.models import Course, Lecture

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(body: CourseCreate, session: SessionDep) -> CourseOut:
    course = Course(title=body.title.strip(), description=body.description)
    session.add(course)
    await session.commit()
    await session.refresh(course)
    return CourseOut.model_validate(course)


@router.get("")
async def list_courses(session: SessionDep) -> list[CourseOut]:
    rows = await session.execute(
        select(Course, func.count(Lecture.id))
        .outerjoin(Lecture, Lecture.course_id == Course.id)
        .group_by(Course.id)
        .order_by(Course.title)
    )
    return [
        CourseOut.model_validate(course).model_copy(update={"lecture_count": count})
        for course, count in rows
    ]


@router.get("/{course_id}")
async def get_course(course_id: uuid.UUID, session: SessionDep) -> CourseDetail:
    course = await course_or_404(session, course_id)
    lectures = (
        await session.scalars(
            select(Lecture).where(Lecture.course_id == course_id).order_by(Lecture.created_at)
        )
    ).all()
    return CourseDetail(
        **CourseOut.model_validate(course).model_dump(exclude={"lecture_count"}),
        lecture_count=len(lectures),
        lectures=[LectureOut.model_validate(lecture) for lecture in lectures],
    )


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: uuid.UUID, session: SessionDep) -> None:
    await session.delete(await course_or_404(session, course_id))
    await session.commit()
