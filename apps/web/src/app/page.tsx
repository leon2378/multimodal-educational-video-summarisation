import { LectureList, UploadForm } from "@/components/library";

export default function Home() {
  return (
    <div className="flex flex-col gap-6">
      <UploadForm />
      <LectureList />
    </div>
  );
}
