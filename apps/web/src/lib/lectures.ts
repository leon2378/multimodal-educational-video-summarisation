import type { QueryClient } from "@tanstack/react-query";

import { type Course, type CourseDetail, type Lecture, api, ensureOk } from "./api";
import { courseKey, lectureKey } from "./queries";

/** Deletes a lecture and everything made from it. 409 while it's processing; 503 when search
 *  can't drop its passages yet, which is worth another go. */
export async function deleteLecture(id: string): Promise<void> {
  ensureOk(await api.DELETE("/v1/lectures/{lecture_id}", { params: { path: { lecture_id: id } } }));
}

/** Takes a deleted lecture out of every cached list at once, so nothing shows it while the
 *  refetches run, then refetches them. */
export async function forgetLecture(queryClient: QueryClient, lecture: Lecture): Promise<void> {
  queryClient.setQueryData<Lecture[]>(["lectures"], (lectures) => lectures?.filter((l) => l.id !== lecture.id));
  const courseId = lecture.course_id;
  if (courseId !== null) {
    queryClient.setQueryData<CourseDetail>(courseKey(courseId), (course) =>
      course && {
        ...course,
        lectures: course.lectures.filter((l) => l.id !== lecture.id),
        lecture_count: Math.max(course.lecture_count - 1, 0),
      },
    );
    queryClient.setQueryData<Course[]>(["courses"], (courses) =>
      courses?.map((c) => (c.id === courseId ? { ...c, lecture_count: Math.max(c.lecture_count - 1, 0) } : c)),
    );
  }
  queryClient.removeQueries({ queryKey: lectureKey(lecture.id) });
  // Library search results may point into it.
  queryClient.removeQueries({ queryKey: ["search"] });
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["lectures"] }),
    queryClient.invalidateQueries({ queryKey: ["courses"] }),
    ...(courseId !== null ? [queryClient.invalidateQueries({ queryKey: courseKey(courseId) })] : []),
  ]);
}
