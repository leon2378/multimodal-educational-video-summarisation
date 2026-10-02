"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  CaptionsIcon,
  ChevronRightIcon,
  ClockIcon,
  DownloadIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  FileQuestionIcon,
  FolderIcon,
  GlobeIcon,
  GraduationCapIcon,
  HistoryIcon,
  LinkIcon,
  LockIcon,
  MessagesSquareIcon,
  NotebookTextIcon,
  PresentationIcon,
  RotateCcwIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { refusal } from "@/lib/access";
import {
  ApiError,
  type Lecture,
  type Slide,
  type StudyNotes,
  type TranscriptLine,
  type Visibility,
  api,
  unwrap,
} from "@/lib/api";
import { formatRelative, sourceSite } from "@/lib/format";
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

import { PrivateBadge, useAccount } from "./account";
import { AskPanel } from "./chat";
import { StatusBadge, loadKatex, useDocumentTitle } from "./common";
import { DeleteLectureDialog, deleteBlocked } from "./delete-lecture";
import { NotesPanel, QuizPanel, downloadNotes } from "./notes";
import { ChapterRail, KeyHints, usePlayerKeys } from "./player";
import { ProcessingPanel, RunsDialog, useStartProcessing } from "./processing";
import { Filmstrip, SlidesPanel } from "./slides";
import { TranscriptPanel } from "./transcript";

type Seek = (seconds: number) => void;

/** A lecture's page. `start` (from ?t=) plays it from that second. */
export function LectureView({ id, start }: { id: string; start?: number }) {
  const router = useRouter();
  // Once it's deleted, nothing asks for it again while the library opens.
  const [deleted, setDeleted] = useState(false);
  const lecture = useLecture(id, !deleted);
  const status = deleted ? undefined : lecture.data?.status;
  const ready = status === "ready";
  // The event stream also reports a finished run once, which is how a failed run's error shows.
  const progress = useProgress(id, status === "processing" || status === "failed");
  // A lecture from a link has no video to play until its download has stored one.
  const downloading = lecture.data?.source_url != null && lecture.data.size_bytes === null;
  const media = useMedia(id, status !== undefined && status !== "awaiting_upload" && !downloading);
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
  // Notes and answers may hold formulas: have KaTeX ready by the time they show.
  useEffect(() => {
    if (ready) void loadKatex();
  }, [ready]);
  const onDeleted = useCallback(() => {
    setDeleted(true);
    router.replace("/");
  }, [router]);

  if (deleted) {
    return (
      <Empty className="min-h-[60vh]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Trash2Icon />
          </EmptyMedia>
          <EmptyTitle>Lecture deleted</EmptyTitle>
          <EmptyDescription>Opening the library…</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline">
            <Link href="/">Back to the library</Link>
          </Button>
        </EmptyContent>
      </Empty>
    );
  }
  if (lecture.isPending) return <LectureSkeleton />;
  if (lecture.isError) {
    // Not found, or not a lecture's address at all.
    const missing = lecture.error instanceof ApiError && [404, 422].includes(lecture.error.status);
    return (
      <Empty className="min-h-[60vh]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileQuestionIcon />
          </EmptyMedia>
          <EmptyTitle>{missing ? "Lecture not found" : "Couldn't open this lecture"}</EmptyTitle>
          <EmptyDescription>
            {missing ? "It may have been deleted, or it's private to someone else." : lecture.error.message}
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
  const info = lecture.data;
  const chapters = notes.data?.notes.chapters ?? [];

  return (
    // The title sits above the video rather than across the page, so the study panel beside
    // them can run the full height of the window.
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_24rem] xl:grid-cols-[minmax(0,1fr)_28rem]">
      <div className="flex min-w-0 flex-col gap-5">
        <LectureHeader
          lecture={info}
          notes={notes.data?.notes}
          slideCount={slides.data?.length}
          time={time}
          onDeleted={onDeleted}
        />
        <div className="overflow-hidden rounded-xl bg-black shadow-sm ring-1 ring-border">
          {media.data ? (
            <video
              ref={video}
              src={media.data.url}
              // The first slide until it plays, rather than a black box.
              poster={slides.data?.[0]?.image_url}
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
              {status === "awaiting_upload"
                ? "The video wasn't uploaded"
                : downloading
                  ? status === "failed"
                    ? "The video wasn't downloaded"
                    : "Downloading the video…"
                  : "Loading the video…"}
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
  onDeleted,
}: {
  lecture: Lecture;
  notes: StudyNotes | undefined;
  slideCount: number | undefined;
  time: number;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const courses = useCourses();
  const account = useAccount();
  const course = courses.data?.find((c) => c.id === lecture.course_id);
  const [history, setHistory] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [removing, setRemoving] = useState(false);
  const { start } = useStartProcessing(lecture.id);
  const ready = lecture.status === "ready";
  // Someone else's public lecture reads the same, without the controls that change it.
  const mine = account.canChange(lecture);
  const blocked = deleteBlocked(lecture);
  const site = sourceSite(lecture.source_url);

  async function setVisibility(visibility: Visibility) {
    try {
      unwrap(
        await api.PATCH("/v1/lectures/{lecture_id}", {
          params: { path: { lecture_id: lecture.id } },
          body: { visibility },
        }),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: lectureKey(lecture.id) }),
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
      ]);
      toast.success(
        visibility === "public" ? "Published: everyone can see it now" : "Made private: only its owner and admins see it",
      );
    } catch (e) {
      toast.error("Couldn't change who sees the lecture", { description: refusal(e) });
    }
  }

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
            <span className="flex gap-1.5">
              <StatusBadge status={lecture.status} />
              <PrivateBadge visibility={lecture.visibility} />
            </span>
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
            {site && lecture.source_url && (
              <a
                href={lecture.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-w-0 items-center gap-1.5 hover:text-foreground"
                title={lecture.source_url}
              >
                <ExternalLinkIcon className="size-3.5 shrink-0" /> <span className="truncate">From {site}</span>
              </a>
            )}
            {(lecture.attribution || lecture.licence) && (
              <span className="truncate" title={[lecture.attribution, lecture.licence].filter(Boolean).join(" · ")}>
                {[lecture.attribution, lecture.licence].filter(Boolean).join(" · ")}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {mine && <CoursePicker lecture={lecture} />}
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
              {((ready && mine) || (account.auth && account.admin)) && <DropdownMenuSeparator />}
              {ready && mine && (
                <DropdownMenuItem onSelect={() => setConfirm(true)}>
                  <RotateCcwIcon /> Process again
                </DropdownMenuItem>
              )}
              {account.auth &&
                account.admin &&
                (lecture.visibility === "private" ? (
                  <DropdownMenuItem onSelect={() => void setVisibility("public")}>
                    <GlobeIcon /> Make public
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem onSelect={() => void setVisibility("private")}>
                    <LockIcon /> Make private
                  </DropdownMenuItem>
                ))}
              {mine && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" disabled={blocked !== null} onSelect={() => setRemoving(true)}>
                    <Trash2Icon /> Delete lecture
                  </DropdownMenuItem>
                  {blocked && <p className="px-2 pb-1.5 text-xs text-muted-foreground">{blocked}</p>}
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <RunsDialog lectureId={lecture.id} open={history} onOpenChange={setHistory} />
      <DeleteLectureDialog lecture={lecture} open={removing} onOpenChange={setRemoving} onDeleted={onDeleted} />
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
  const account = useAccount();
  const current = courses.data?.find((course) => course.id === lecture.course_id);
  // A lecture can join only a course the viewer may change; its current one stays listed.
  const joinable = (courses.data ?? []).filter((course) => account.canChange(course) || course.id === current?.id);

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
      toast.error("Couldn't move the lecture", { description: refusal(e) });
    }
  }

  if (!courses.data || (joinable.length === 0 && !lecture.course_id)) return null;
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
          {joinable.map((course) => (
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
  // Words only where the panel is narrow (a phone, or beside the video on a smaller laptop),
  // so all five tabs fit.
  const trigger = "flex-none px-2.5 max-sm:px-2 max-sm:[&_svg]:hidden lg:max-xl:px-2 lg:max-xl:[&_svg]:hidden";

  return (
    <Tabs
      value={selected}
      onValueChange={setSelected}
      className="h-full gap-0 overflow-hidden rounded-xl border bg-card shadow-sm"
    >
      <div className="overflow-x-auto border-b px-2 scrollbar-none">
        <TabsList variant="line" className="h-11 w-full justify-start gap-0">
          <TabsTrigger value="notes" className={trigger}>
            <NotebookTextIcon /> Notes
          </TabsTrigger>
          <TabsTrigger value="transcript" className={trigger}>
            <CaptionsIcon /> Transcript
          </TabsTrigger>
          <TabsTrigger value="slides" className={trigger}>
            <PresentationIcon /> Slides
          </TabsTrigger>
          <TabsTrigger value="quiz" className={trigger}>
            <GraduationCapIcon /> Quiz
          </TabsTrigger>
          <TabsTrigger value="ask" className={trigger}>
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
        <AskPanel scope={scope} onOpen={onOpen} suggestions={suggestions} />
      </TabsContent>
    </Tabs>
  );
}

/** Questions to start a conversation from, taken from the lecture's own notes. */
function suggest(notes: StudyNotes): string[] {
  const [first, second] = notes.concepts;
  return [
    "Summarise this lecture in five points.",
    first && `What is “${first.term}”, and why does it matter here?`,
    second && `Give an example of “${second.term}” from the lecture.`,
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
