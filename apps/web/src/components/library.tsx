"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  CaptionsIcon,
  FolderPlusIcon,
  LibraryIcon,
  MessagesSquareIcon,
  NotebookTextIcon,
  SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { refusal } from "@/lib/access";
import { type Course, type Lecture, api, unwrap } from "@/lib/api";
import { formatDuration, formatRelative, hueFor, pluralise } from "@/lib/format";
import { useCourses, useLectures } from "@/lib/queries";
import { lectureHref } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { PrivateBadge, SignInPrompt, useAccount } from "./account";
import { Callout, LectureCover, StatusBadge, useDocumentTitle } from "./common";
import { DropTarget } from "./upload";

type Filter = "all" | "yours" | "ready" | "processing" | "attention";

const BY_STATUS: Record<Exclude<Filter, "yours">, (lecture: Lecture) => boolean> = {
  all: () => true,
  ready: (lecture) => lecture.status === "ready",
  processing: (lecture) => lecture.status === "processing",
  attention: (lecture) => lecture.status !== "ready" && lecture.status !== "processing",
};

export function LibraryView() {
  useDocumentTitle("Library");
  const lectures = useLectures();
  const courses = useCourses();
  const account = useAccount();
  const [filter, setFilter] = useState<Filter>("all");
  const [text, setText] = useState("");
  const yours = (lecture: Lecture) => account.user !== null && lecture.owner_id === account.user.id;
  const matches = (f: Filter, lecture: Lecture) => (f === "yours" ? yours(lecture) : BY_STATUS[f](lecture));

  if (lectures.isPending) return <LibrarySkeleton />;
  if (lectures.isError) {
    return (
      <Callout tone="error" title="Couldn't load the library">
        {lectures.error.message}
      </Callout>
    );
  }
  if (lectures.data.length === 0 && (courses.data?.length ?? 0) === 0) return <Welcome />;

  const all = lectures.data;
  const seconds = all.reduce((sum, lecture) => sum + (lecture.duration_s ?? 0), 0);
  const courseTitles = new Map((courses.data ?? []).map((course) => [course.id, course.title]));
  const needle = text.trim().toLowerCase();
  const shown = all.filter(
    (lecture) =>
      matches(filter, lecture) &&
      (!needle ||
        lecture.title.toLowerCase().includes(needle) ||
        (courseTitles.get(lecture.course_id ?? "") ?? "").toLowerCase().includes(needle)),
  );
  const count = (f: Filter) => all.filter((lecture) => matches(f, lecture)).length;

  return (
    <div className="flex flex-col gap-10">
      <div className="flex flex-col gap-1">
        <h1 className="text-3xl font-semibold tracking-tight">Library</h1>
        <p className="text-muted-foreground">
          {pluralise(all.length, "lecture")}
          {seconds > 0 && ` · ${formatDuration(seconds)} of video`}
          {courses.data && ` · ${pluralise(courses.data.length, "course")}`}
        </p>
      </div>

      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">Courses</h2>
          <NewCourseButton />
        </div>
        {courses.data && courses.data.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {courses.data.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Group lectures into a course to search and ask questions across all of them.
          </p>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <h2 className="text-lg font-semibold tracking-tight md:mr-auto">Lectures</h2>
          <div className="relative md:w-64">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Filter by title or course"
              aria-label="Filter lectures"
              className="pl-8"
            />
          </div>
          <ToggleGroup
            type="single"
            variant="outline"
            value={filter}
            onValueChange={(value) => value && setFilter(value as Filter)}
            aria-label="Show lectures"
            className="overflow-x-auto"
          >
            <ToggleGroupItem value="all" className="px-3">
              All <Count n={all.length} />
            </ToggleGroupItem>
            {account.auth && count("yours") > 0 && (
              <ToggleGroupItem value="yours" className="px-3">
                Yours <Count n={count("yours")} />
              </ToggleGroupItem>
            )}
            <ToggleGroupItem value="ready" className="px-3">
              Ready <Count n={count("ready")} />
            </ToggleGroupItem>
            {count("processing") > 0 && (
              <ToggleGroupItem value="processing" className="px-3">
                Processing <Count n={count("processing")} />
              </ToggleGroupItem>
            )}
            {count("attention") > 0 && (
              <ToggleGroupItem value="attention" className="px-3">
                Needs attention <Count n={count("attention")} />
              </ToggleGroupItem>
            )}
          </ToggleGroup>
        </div>
        {all.length === 0 ? (
          <DropTarget />
        ) : shown.length === 0 ? (
          <p className="rounded-xl border border-dashed py-12 text-center text-sm text-muted-foreground">
            No lectures match.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-x-5 gap-y-7 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {shown.map((lecture) => (
              <LectureCard key={lecture.id} lecture={lecture} course={courseTitles.get(lecture.course_id ?? "")} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Count({ n }: { n: number }) {
  return <span className="text-xs text-muted-foreground tabular-nums">{n}</span>;
}

function LectureCard({ lecture, course }: { lecture: Lecture; course?: string }) {
  return (
    <Link
      href={lectureHref(lecture.id)}
      className="group flex flex-col gap-3 rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <LectureCover
        lecture={lecture}
        className="aspect-video rounded-xl shadow-sm ring-1 ring-border transition group-hover:shadow-md group-hover:ring-primary/40"
      >
        <span className="absolute top-2 left-2 flex gap-1.5">
          {lecture.status !== "ready" && <StatusBadge status={lecture.status} className="shadow-sm backdrop-blur" />}
          <PrivateBadge visibility={lecture.visibility} className="shadow-sm" />
        </span>
        {lecture.duration_s != null && (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 font-mono text-xs text-white tabular-nums">
            {formatTime(lecture.duration_s)}
          </span>
        )}
      </LectureCover>
      <div className="flex flex-col gap-1 px-0.5">
        <h3 className="line-clamp-2 leading-snug font-medium transition-colors group-hover:text-primary">
          {lecture.title}
        </h3>
        <p className="truncate text-sm text-muted-foreground">
          {[course, formatRelative(lecture.created_at)].filter(Boolean).join(" · ")}
        </p>
      </div>
    </Link>
  );
}

function CourseCard({ course }: { course: Course }) {
  const hue = hueFor(course.id);
  return (
    <Link
      href={`/courses/${course.id}`}
      className="group flex items-center gap-4 rounded-xl border bg-card p-4 shadow-xs transition hover:border-primary/40 hover:shadow-md focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <span
        className="flex size-11 shrink-0 items-center justify-center rounded-lg text-white shadow-sm"
        style={{ backgroundImage: `linear-gradient(135deg, oklch(0.7 0.13 ${hue}), oklch(0.5 0.16 ${(hue + 50) % 360}))` }}
      >
        <LibraryIcon className="size-5" />
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium transition-colors group-hover:text-primary">{course.title}</span>
          <PrivateBadge visibility={course.visibility} className="shrink-0" />
        </span>
        <span className="truncate text-sm text-muted-foreground">
          {course.description || pluralise(course.lecture_count, "lecture")}
        </span>
      </span>
      {course.description && (
        <span className="ml-auto shrink-0 text-sm text-muted-foreground tabular-nums">{course.lecture_count}</span>
      )}
    </Link>
  );
}

export function NewCourseButton({ variant = "outline" }: { variant?: "outline" | "default" }) {
  const queryClient = useQueryClient();
  const account = useAccount();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  // Only admins make public courses; everyone else's are private to them.
  const [isPublic, setIsPublic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signIn = account.auth && !account.signedIn;
  const choosesVisibility = account.auth && account.admin;

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const course = unwrap(
        await api.POST("/v1/courses", {
          body: {
            title: title.trim(),
            description: description.trim() || null,
            visibility: choosesVisibility ? (isPublic ? "public" : "private") : null,
          },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ["courses"] });
      toast.success(`Created ${course.title}`);
      setOpen(false);
      setTitle("");
      setDescription("");
      setIsPublic(false);
    } catch (e) {
      setError(refusal(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant={variant} size="sm" onClick={() => setOpen(true)}>
        <FolderPlusIcon />
        New course
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New course</DialogTitle>
            <DialogDescription>
              A course groups lectures, so you can search and ask questions across all of them.
            </DialogDescription>
          </DialogHeader>
          {signIn ? (
            <SignInPrompt title="Sign in to make courses">
              Your courses are private to you, and can hold your lectures.
            </SignInPrompt>
          ) : (
            <form id="new-course" onSubmit={create} className="flex flex-col gap-4">
              <div className="grid gap-2">
                <Label htmlFor="course-title">Title</Label>
                <Input
                  id="course-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="MIT 6.0001 Fall 2016"
                  maxLength={300}
                  required
                  autoFocus
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="course-description">
                  Description <span className="font-normal text-muted-foreground">(optional)</span>
                </Label>
                <Textarea
                  id="course-description"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="Introduction to Computer Science and Programming in Python"
                  maxLength={2000}
                  rows={3}
                />
              </div>
              {choosesVisibility && (
                <label className="flex items-start gap-2.5 text-sm">
                  <input
                    type="checkbox"
                    checked={isPublic}
                    onChange={(event) => setIsPublic(event.target.checked)}
                    className="mt-0.5 size-4 accent-primary"
                  />
                  <span>
                    <span className="font-medium">Public</span>
                    <span className="block text-muted-foreground">
                      Everyone can see it, signed in or not. Otherwise only you can.
                    </span>
                  </span>
                </label>
              )}
              {error && <Callout tone="error">{error}</Callout>}
            </form>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              {signIn ? "Close" : "Cancel"}
            </Button>
            {!signIn && (
              <Button type="submit" form="new-course" disabled={!title.trim() || busy}>
                Create course
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

const FEATURES = [
  {
    icon: CaptionsIcon,
    title: "Transcript and slides",
    text: "Speech is transcribed and every slide is read, formulas and code included, all in step with the video.",
  },
  {
    icon: NotebookTextIcon,
    title: "Notes and a quiz",
    text: "Chapters, key concepts and formulas, with each point linked to the moment it's explained.",
  },
  {
    icon: MessagesSquareIcon,
    title: "Search and ask",
    text: "Find what was said, or ask a question and get an answer that cites where in the lecture it comes from.",
  },
];

function Welcome() {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-10 py-6 lg:py-12">
      <div className="flex flex-col items-center gap-3 text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          Turn lecture videos into notes you can study from
        </h1>
        <p className="max-w-2xl text-balance text-muted-foreground">
          Add a recorded lecture and get a synced transcript, the slides&apos; text, chaptered notes, a quiz, and
          answers that cite the moment they come from.
        </p>
      </div>
      <DropTarget className="py-16" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {FEATURES.map(({ icon: Icon, title, text }) => (
          <div key={title} className="flex flex-col gap-2 rounded-xl border bg-card p-5">
            <Icon className="size-5 text-primary" />
            <h2 className="font-medium">{title}</h2>
            <p className="text-sm text-muted-foreground">{text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function LibrarySkeleton() {
  return (
    <div className="flex flex-col gap-10" aria-busy>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-5 w-72" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
      </div>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="flex flex-col gap-3">
            <Skeleton className="aspect-video rounded-xl" />
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ))}
      </div>
    </div>
  );
}
