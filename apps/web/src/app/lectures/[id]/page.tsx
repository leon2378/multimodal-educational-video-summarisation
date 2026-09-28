import { LectureView } from "@/components/lecture";

export default async function LecturePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LectureView id={id} />;
}
