"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  ChevronRightIcon,
  EllipsisIcon,
  FileQuestionIcon,
  LibraryIcon,
  MessagesSquareIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { refusal } from "@/lib/access";
import { ApiError, api, ensureOk } from "@/lib/api";
import { formatDuration, hueFor, pluralise } from "@/lib/format";
import { useCourse } from "@/lib/queries";
import { type Open, lectureHref } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { PrivateBadge, useAccount } from "./account";
import { AskPanel } from "./chat";
import { LectureCover, StatusBadge, useDocumentTitle } from "./common";
import { LectureMenu } from "./delete-lecture";
import { SearchBox, SearchResults } from "./search";
import { DropTarget, useUpload } from "./upload";

const SUGGESTIONS = [
  "What topics do these lectures cover?",
  "Which ideas come up in more than one lecture?",
  "Summarise each lecture in one sentence.",
];

/** A course: its lectures, then search and Q&A across all of them. Results and citations open
 *  the lecture they point into, from that moment. */
export function CourseView({ id }: { id: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const course = useCourse(id);
  const { openUpload } = useUpload();
  const account = useAccount();
  const [confirm, setConfirm] = useState(false);
  const [query, setQuery] = useState("");
  const open = useCallback<Open>((lectureId, seconds) => router.push(lectureHref(lectureId, seconds)), [router]);
  const scope = useMemo(() => ({ kind: "course", id }) as const, [id]);
  useDocumentTitle(course.data?.title);

  async function remove() {
    try {
      ensureOk(await api.DELETE("/v1/courses/{course_id}", { params: { path: { course_id: id } } }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
      ]);
      toast.success(`Deleted ${course.data?.title ?? "the course"}`);
      router.push("/");
    } catch (e) {
      toast.error("Couldn't delete the course", { description: refusal(e) });
    }
  }

  if (course.isPending) {
    return (
      <div className="flex flex-col gap-6" aria-busy>
        <Skeleton className="h-20 w-2/3" />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Skeleton className="h-96 rounded-xl" />
          <Skeleton className="h-96 rounded-xl" />
        </div>
      </div>
    );
  }
  if (course.isError) {
    // Not found, or not a course's address at all.
    const missing = course.error instanceof ApiError && [404, 422].includes(course.error.status);
    return (
      <Empty className="min-h-[60vh]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileQuestionIcon />
          </EmptyMedia>
          <EmptyTitle>{missing ? "Course not found" : "Couldn't open this course"}</EmptyTitle>
          <EmptyDescription>
            {missing ? "It may have been deleted, or it's private to someone else." : course.error.message}
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline">
            <Link href="/">Back to the library</Link>
          </Button>
        </EmptyContent>
      </Empty>
    );
  }
  const info = course.data;
  const titles = Object.fromEntries(info.lectures.map((lecture) => [lecture.id, lecture.title]));
  const ready = info.lectures.filter((lecture) => lecture.status === "ready").length;
  const seconds = info.lectures.reduce((sum, lecture) => sum + (lecture.duration_s ?? 0), 0);
  const hue = hueFor(info.id);
  // Adding lectures and deleting are for its owner (or an admin); anyone who can see it reads it.
  const mine = account.canChange(info);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <header className="flex flex-col gap-3">
            <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-muted-foreground">
              <Link href="/" className="hover:text-foreground">
                Library
              </Link>
              <ChevronRightIcon className="size-3.5" />
              <span className="truncate">Course</span>
            </nav>
            <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
              <div className="flex min-w-0 flex-[1_1_18rem] items-start gap-4">
                <span
                  className="flex size-14 shrink-0 items-center justify-center rounded-xl text-white shadow-sm"
                  style={{ backgroundImage: `linear-gradient(135deg, oklch(0.7 0.13 ${hue}), oklch(0.5 0.16 ${(hue + 50) % 360}))` }}
                >
                  <LibraryIcon className="size-6" />
                </span>
                <div className="flex min-w-0 flex-col gap-1">
                  <h1 className="text-2xl font-semibold tracking-tight text-balance sm:text-3xl">{info.title}</h1>
                  {info.description && <p className="text-muted-foreground">{info.description}</p>}
                  <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                    <PrivateBadge visibility={info.visibility} />
                    {pluralise(info.lecture_count, "lecture")}
                    {seconds > 0 && ` · ${formatDuration(seconds)}`}
                    {info.lecture_count > 0 && ` · ${ready} ready`}
                  </p>
                </div>
              </div>
              {mine && (
                <div className="flex items-center gap-2">
                  <Button onClick={() => openUpload({ courseId: id })}>
                    <PlusIcon /> Add lecture
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="icon" aria-label="More actions">
                        <EllipsisIcon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem variant="destructive" onSelect={() => setConfirm(true)}>
                        <Trash2Icon /> Delete course
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )}
            </div>
          </header>
          <section className="flex flex-col gap-3">
            <h2 className="text-lg font-semibold tracking-tight">Lectures</h2>
            {info.lectures.length === 0 && !mine ? (
              <p className="text-sm text-muted-foreground">No lectures in this course yet.</p>
            ) : info.lectures.length === 0 ? (
              <DropTarget courseId={id} />
            ) : (
              <ol className="flex flex-col gap-2">
                {info.lectures.map((lecture, index) => (
                  <li key={lecture.id} className="group/card relative">
                    <Link
                      href={lectureHref(lecture.id)}
                      className={cn(
                        "group flex items-center gap-4 rounded-xl border bg-card p-2.5 shadow-xs transition hover:border-primary/40 hover:shadow-md",
                        // Room for its menu.
                        account.canChange(lecture) ? "pr-12" : "pr-4",
                      )}
                    >
                      <LectureCover lecture={lecture} className="aspect-video w-32 shrink-0 rounded-lg ring-1 ring-border">
                        {lecture.duration_s != null && (
                          <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 font-mono text-[0.65rem] text-white">
                            {formatTime(lecture.duration_s)}
                          </span>
                        )}
                      </LectureCover>
                      <div className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="text-xs text-muted-foreground tabular-nums">Lecture {index + 1}</span>
                        <span className="line-clamp-2 leading-snug font-medium transition-colors group-hover:text-primary">
                          {lecture.title}
                        </span>
                        <span className="flex gap-1.5">
                          {lecture.status !== "ready" && <StatusBadge status={lecture.status} />}
                          <PrivateBadge visibility={lecture.visibility} />
                        </span>
                      </div>
                    </Link>
                    {account.canChange(lecture) && (
                      <LectureMenu lecture={lecture} className="absolute top-1/2 right-3 -translate-y-1/2" />
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>

        <section className="h-[80dvh] min-h-[28rem] lg:sticky lg:top-[5.5rem] lg:h-[calc(100dvh-7.5rem)]">
          {ready > 0 ? (
            <Tabs defaultValue="ask" className="h-full gap-0 overflow-hidden rounded-xl border bg-card shadow-sm">
              <div className="border-b px-2">
                <TabsList variant="line" className="h-11 justify-start gap-0">
                  <TabsTrigger value="ask" className="flex-none px-2.5">
                    <MessagesSquareIcon /> Ask the course
                  </TabsTrigger>
                  <TabsTrigger value="search" className="flex-none px-2.5">
                    <SearchIcon /> Search the course
                  </TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="ask" forceMount className="min-h-0 flex-1 data-[state=inactive]:hidden">
                <AskPanel scope={scope} onOpen={open} suggestions={SUGGESTIONS} />
              </TabsContent>
              <TabsContent
                value="search"
                forceMount
                className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
              >
                <div className="border-b p-3">
                  <SearchBox placeholder="Search every lecture in this course" onSearch={setQuery} />
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  {query ? (
                    <SearchResults scope={scope} query={query} onOpen={open} titles={titles} />
                  ) : (
                    <p className="py-8 text-center text-sm text-muted-foreground">
                      Search what was said and shown across {pluralise(ready, "lecture")}.
                    </p>
                  )}
                </div>
              </TabsContent>
            </Tabs>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center">
              <MessagesSquareIcon className="size-6 text-muted-foreground" />
              <p className="font-medium">Search and Q&amp;A across the course</p>
              <p className="max-w-xs text-sm text-muted-foreground">
                These start once one of the course&apos;s lectures has been processed.
              </p>
            </div>
          )}
        </section>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{info.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {info.lecture_count > 0
                ? `Its ${pluralise(info.lecture_count, "lecture")} stay in your library, out of any course. `
                : ""}
              Conversations about the course are deleted. This can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void remove()}>
              Delete course
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
