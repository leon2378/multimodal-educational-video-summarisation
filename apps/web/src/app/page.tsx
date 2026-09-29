import { CourseList, LectureList, UploadForm } from "@/components/library";

export default function Home() {
  return (
    <div className="flex flex-col gap-6">
      <UploadForm />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <LectureList />
        </div>
        <CourseList />
      </div>
    </div>
  );
}
