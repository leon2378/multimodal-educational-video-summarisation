"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  CaptionsIcon,
  ChevronRightIcon,
  ClockIcon,
  DownloadIcon,
  EllipsisIcon,
  FileQuestionIcon,
  FolderIcon,
  GraduationCapIcon,
  HistoryIcon,
  LinkIcon,
  MessagesSquareIcon,
  NotebookTextIcon,
  PresentationIcon,
  RotateCcwIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";
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
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type Lecture, type Slide, type StudyNotes, type TranscriptLine, api, unwrap } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import {
  courseKey,
  lectureKey,
  useCourses,
  useLecture,
  useMedia,
  useNotes,
  useProgress,
  useSlides,
  useTranscript,
} from "@/lib/queries";
import { type Open, lectureHref } from "@/lib/scope";
import { formatTime, indexAt, slideAt } from "@/lib/timeline";

import { ChatPanel } from "./chat";
import { StatusBadge, useDocumentTitle } from "./common";
import { NotesPanel, QuizPanel, downloadNotes } from "./notes";
import { ChapterRail, KeyHints, usePlayerKeys } from "./player";
import { ProcessingPanel, RunsDialog, useStartProcessing } from "./processing";
import { Filmstrip, SlidesPanel } from "./slides";
import { TranscriptPanel } from "./transcript";

type Seek = (seconds: number) => void;

/** A lecture's page. `start` (from ?t=) plays it from that second. */
export function LectureView({ id, start }: { id: string; start?: number }) {
  const router = useRouter();
  const lecture = useLecture(id);
  const status = lecture.data?.status;
  const ready = status === "ready";
  // The event stream also reports a finished run once, which is how a failed run's error shows.
  const progress = useProgress(id, status === "processing" || status === "failed");
  const media = useMedia(id, status !== undefined && status !== "awaiting_upload");
  const transcript = useTranscript(id, ready);
  const slides = useSlides(id, ready);
  const notes = useNotes(id, ready);
  useDocumentTitle(lecture.data?.title);

  const video = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const seek = useCallback<Seek>((seconds) => {
    const player = video.current;
    if (!player) return;
    player.currentTime = seconds;
    setTime(seconds);
    // Browsers may block playing without a click on this page; the seek still happens.
    player.play().catch(() => undefined);
  }, []);
  // Citations from a course's answers can point into another lecture: open it there.
  const open = useCallback<Open>(
    (lectureId, seconds) => (lectureId === id ? seek(seconds) : router.push(lectureHref(lectureId, seconds))),
    [id, seek, router],
  );
  const started = useRef(false);
  usePlayerKeys(video);

  if (lecture.isPending) return <LectureSkeleton />;
  if (lecture.isError) {
    return (
      <Empty className="min-h-[60vh]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileQuestionIcon />
          </EmptyMedia>
          <EmptyTitle>Couldn&apos;t open this lecture</EmptyTitle>
          <EmptyDescription>{lecture.error.message}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline">
            <Link href="/">Back to the library</Link>
          </Button>
        </EmptyContent>
      </Empty>
    );
  }
  const info = lecture.data;
  const chapters = notes.data?.notes.chapters ?? [];

  return (
    // The title sits above the video rather than across the page, so the study panel beside
    // them can run the full height of the window.
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_24rem] xl:grid-cols-[minmax(0,1fr)_28rem]">
      <div className="flex min-w-0 flex-col gap-5">
        <LectureHeader lecture={info} notes={notes.data?.notes} slideCount={slides.data?.length} time={time} />
        <div className="overflow-hidden rounded-xl bg-black shadow-sm ring-1 ring-border">
          {media.data ? (
            <video
              ref={video}
              src={media.data.url}
              controls
              playsInline
              preload="metadata"
              className="aspect-video w-full"
              onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
              onLoadedMetadata={() => {
                if (start !== undefined && !started.current) {
                  started.current = true;
                  seek(start);
                }
              }}
            />
          ) : (
            <div className="flex aspect-video items-center justify-center text-sm text-white/60">
              {status === "awaiting_upload" ? "The video wasn't uploaded" : "Loading the video…"}
            </div>
          )}
        </div>
        {chapters.length > 0 && (
          <ChapterRail chapters={chapters} current={indexAt(chapters, time)} time={time} onSeek={seek} />
        )}
        {slides.data && slides.data.length > 0 && (
          <Filmstrip slides={slides.data} current={slideAt(slides.data, time)} onSeek={seek} />
        )}
        {ready && <KeyHints />}
      </div>

      <aside className="h-[80dvh] min-h-[28rem] lg:sticky lg:top-[5.5rem] lg:h-[calc(100dvh-7.5rem)]">
        {ready ? (
          notes.data && transcript.data && slides.data ? (
            <StudyPanel
              lecture={info}
              notes={notes.data.notes}
              transcript={transcript.data}
              slides={slides.data}
              time={time}
              onSeek={seek}
              onOpen={open}
            />
          ) : (
            <Skeleton className="h-full rounded-xl" />
          )
        ) : (
          <ProcessingPanel lecture={info} event={progress} />
        )}
      </aside>
    </div>
  );
}

function LectureHeader({
  lecture,
  notes,
  slideCount,
  time,
}: {
  lecture: Lecture;
  notes: StudyNotes | undefined;
  slideCount: number | undefined;
  time: number;
}) {
  const courses = useCourses();
  const course = courses.data?.find((c) => c.id === lecture.course_id);
  const [history, setHistory] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const { start } = useStartProcessing(lecture.id);
  const ready = lecture.status === "ready";

  const copyLink = async () => {
    const seconds = Math.floor(time);
    await navigator.clipboard.writeText(`${window.location.origin}${lectureHref(lecture.id, seconds || undefined)}`);
    toast.success("Link copied", { description: seconds ? `It opens the lecture at ${formatTime(seconds)}.` : undefined });
  };

  return (
    <header className="flex flex-col gap-3">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-muted-foreground">
        <Link href="/" className="hover:text-foreground">
          Library
        </Link>
        {course && (
          <>
            <ChevronRightIcon className="size-3.5" />
            <Link href={`/courses/${course.id}`} className="truncate hover:text-foreground">
              {course.title}
            </Link>
          </>
        )}
      </nav>
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-[1_1_18rem] flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-balance sm:text-3xl">{lecture.title}</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
            <StatusBadge status={lecture.status} />
            {lecture.duration_s != null && (
              <span className="flex items-center gap-1.5 tabular-nums">
                <ClockIcon className="size-3.5" /> {formatTime(lecture.duration_s)}
              </span>
            )}
            {slideCount !== undefined && slideCount > 0 && (
              <span className="flex items-center gap-1.5">
                <PresentationIcon className="size-3.5" /> {slideCount} slides
              </span>
            )}
            <span>Added {formatRelative(lecture.created_at)}</span>
            {(lecture.attribution || lecture.licence) && (
              <span className="truncate">{[lecture.attribution, lecture.licence].filter(Boolean).join(" · ")}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <CoursePicker lecture={lecture} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" aria-label="More actions">
                <EllipsisIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
              {ready && notes && (
                <DropdownMenuItem onSelect={() => downloadNotes(notes, lecture.title)}>
                  <DownloadIcon /> Download notes (.md)
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => void copyLink()}>
                <LinkIcon /> {time >= 1 ? `Copy link at ${formatTime(time)}` : "Copy link"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setHistory(true)}>
                <HistoryIcon /> Processing history
              </DropdownMenuItem>
              {ready && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setConfirm(true)}>
                    <RotateCcwIcon /> Process again
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <RunsDialog lectureId={lecture.id} open={history} onOpenChange={setHistory} />
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Process this lecture again?</AlertDialogTitle>
            <AlertDialogDescription>
              Stages whose inputs haven&apos;t changed come from the cache, so this is quick unless the pipeline has
              changed. The notes and Q&amp;A are unavailable until it finishes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void start()}>Process again</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </header>
  );
}

/** Which course the lecture is in, and a way to move it. */
function CoursePicker({ lecture }: { lecture: Lecture }) {
  const queryClient = useQueryClient();
  const courses = useCourses();
  const current = courses.data?.find((course) => course.id === lecture.course_id);

  async function move(courseId: string | null) {
    try {
      unwrap(
        await api.PATCH("/v1/lectures/{lecture_id}", {
          params: { path: { lecture_id: lecture.id } },
          body: { course_id: courseId },
        }),
      );
      const changed = [lecture.course_id, courseId].filter((c): c is string => c !== null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: lectureKey(lecture.id) }),
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
        ...changed.map((c) => queryClient.invalidateQueries({ queryKey: courseKey(c) })),
      ]);
      const title = courses.data?.find((course) => course.id === courseId)?.title;
      toast.success(title ? `Moved to ${title}` : "Taken out of its course");
    } catch (e) {
      toast.error("Couldn't move the lecture", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  if (!courses.data || (courses.data.length === 0 && !lecture.course_id)) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="max-w-64">
          <FolderIcon />
          <span className="truncate">{current?.title ?? "Add to a course"}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Course</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={lecture.course_id ?? "none"}
          onValueChange={(value) => void move(value === "none" ? null : value)}
        >
          {courses.data.map((course) => (
            <DropdownMenuRadioItem key={course.id} value={course.id}>
              <span className="truncate">{course.title}</span>
            </DropdownMenuRadioItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuRadioItem value="none">No course</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The tabs beside the video. They stay mounted when hidden, so an answer keeps streaming and
 *  a search stays put while you look at another tab. */
function StudyPanel({
  lecture,
  notes,
  transcript,
  slides,
  time,
  onSeek,
  onOpen,
}: {
  lecture: Lecture;
  notes: StudyNotes;
  transcript: TranscriptLine[];
  slides: Slide[];
  time: number;
  onSeek: Seek;
  onOpen: Open;
}) {
  const scope = useMemo(() => ({ kind: "lecture", id: lecture.id }) as const, [lecture.id]);
  const suggestions = useMemo(() => suggest(notes), [notes]);
  const [selected, setSelected] = useState("notes");
  const tab = "min-h-0 flex-1 data-[state=inactive]:hidden";

  return (
    <Tabs
      value={selected}
      onValueChange={setSelected}
      className="h-full gap-0 overflow-hidden rounded-xl border bg-card shadow-sm"
    >
      <div className="overflow-x-auto border-b px-2 scrollbar-none">
        <TabsList variant="line" className="h-11 w-full justify-start gap-0">
          <TabsTrigger value="notes" className="flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden">
            <NotebookTextIcon /> Notes
          </TabsTrigger>
          <TabsTrigger value="transcript" className="flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden">
            <CaptionsIcon /> Transcript
          </TabsTrigger>
          <TabsTrigger value="slides" className="flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden">
            <PresentationIcon /> Slides
          </TabsTrigger>
          <TabsTrigger value="quiz" className="flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden">
            <GraduationCapIcon /> Quiz
          </TabsTrigger>
          <TabsTrigger value="ask" className="flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden">
            <MessagesSquareIcon /> Ask
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="notes" forceMount className={`${tab} overflow-y-auto`}>
        <NotesPanel notes={notes} title={lecture.title} currentChapter={indexAt(notes.chapters, time)} onSeek={onSeek} />
      </TabsContent>
      <TabsContent value="transcript" forceMount className={tab}>
        <TranscriptPanel
          lectureId={lecture.id}
          lines={transcript}
          chapters={notes.chapters}
          current={indexAt(transcript, time)}
          visible={selected === "transcript"}
          onSeek={onSeek}
          onOpen={onOpen}
        />
      </TabsContent>
      <TabsContent value="slides" forceMount className={tab}>
        <SlidesPanel
          slides={slides}
          current={slideAt(slides, time)}
          visible={selected === "slides"}
          onSeek={onSeek}
        />
      </TabsContent>
      <TabsContent value="quiz" forceMount className={`${tab} overflow-y-auto`}>
        <QuizPanel lectureId={lecture.id} quiz={notes.quiz} onSeek={onSeek} />
      </TabsContent>
      <TabsContent value="ask" forceMount className={tab}>
        <ChatPanel scope={scope} onOpen={onOpen} suggestions={suggestions} />
      </TabsContent>
    </Tabs>
  );
}

/** Questions to start a conversation from, taken from the lecture's own notes. */
function suggest(notes: StudyNotes): string[] {
  const [first, second] = notes.concepts;
  return [
    "Summarise this lecture in five points.",
    first && `What is ${first.term}, and why does it matter here?`,
    second && `Give an example of ${second.term} from the lecture.`,
    notes.quiz[0]?.question,
  ].filter((s): s is string => Boolean(s));
}

function LectureSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_24rem] xl:grid-cols-[minmax(0,1fr)_28rem]" aria-busy>
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-9 w-2/3" />
          <Skeleton className="h-5 w-80" />
        </div>
        <Skeleton className="aspect-video rounded-xl" />
      </div>
      <Skeleton className="h-[80dvh] min-h-[28rem] rounded-xl lg:h-[calc(100dvh-7.5rem)]" />
    </div>
  );
}
