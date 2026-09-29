import { LectureView } from "@/components/lecture";
import { startTime } from "@/lib/scope";

export default async function LecturePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ t?: string | string[] }>;
}) {
  const { id } = await params;
  const { t } = await searchParams;
  return <LectureView id={id} start={startTime(t)} />;
}
