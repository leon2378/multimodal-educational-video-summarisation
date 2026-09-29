import { CourseView } from "@/components/course";

export default async function CoursePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CourseView id={id} />;
}
