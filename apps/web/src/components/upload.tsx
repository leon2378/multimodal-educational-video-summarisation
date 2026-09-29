"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDownIcon, CloudUploadIcon, FileVideoIcon, XIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, createContext, use, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, unwrap } from "@/lib/api";
import { formatBytes, formatDuration, titleFromFilename } from "@/lib/format";
import { useCourses } from "@/lib/queries";
import { formatTime } from "@/lib/timeline";

import { Callout } from "./common";

interface UploadOptions {
  courseId?: string | null;
  file?: File;
}

const UploadContext = createContext<{ openUpload: (options?: UploadOptions) => void }>({
  openUpload: () => {},
});

export function useUpload() {
  return use(UploadContext);
}

/** The upload dialog, which any page can open, and a drop target over the whole window. */
export function UploadProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<UploadOptions>({});
  // Bumped per opening, so the dialog starts from a clean form.
  const [session, setSession] = useState(0);
  const [dragging, setDragging] = useState(false);
  const busy = useRef(false);

  const openUpload = (next: UploadOptions = {}) => {
    if (busy.current) {
      setOpen(true);
      return;
    }
    setOptions(next);
    setSession((n) => n + 1);
    setOpen(true);
  };

  useEffect(() => {
    let depth = 0;
    const hasFiles = (event: DragEvent) => event.dataTransfer?.types.includes("Files") ?? false;
    const enter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth += 1;
      setDragging(true);
    };
    const leave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const over = (event: DragEvent) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      const file = event.dataTransfer?.files[0];
      if (!file) return;
      if (!file.type.startsWith("video/")) {
        toast.error(`${file.name} isn't a video file.`);
        return;
      }
      if (busy.current) {
        toast.info("An upload is already in progress.");
        return;
      }
      setOptions((current) => ({ courseId: current.courseId, file }));
      setSession((n) => n + 1);
      setOpen(true);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, []);

  return (
    <UploadContext value={{ openUpload }}>
      {children}
      <UploadDialog
        key={session}
        open={open}
        onOpenChange={setOpen}
        initial={options}
        onBusyChange={(value) => {
          busy.current = value;
        }}
      />
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm animate-in fade-in-0">
          <div className="flex w-full max-w-lg flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary bg-card/90 bg-dots px-8 py-14 text-center shadow-xl">
            <CloudUploadIcon className="size-10 text-primary" />
            <p className="text-lg font-semibold">Drop the video to add it</p>
            <p className="text-sm text-muted-foreground">It uploads straight to storage, then processing starts.</p>
          </div>
        </div>
      )}
    </UploadContext>
  );
}

type Phase =
  | { kind: "edit" }
  | { kind: "uploading"; loaded: number; total: number; rate: number }
  | { kind: "starting" }
  | { kind: "error"; message: string };

interface UploadTarget {
  method: string;
  url: string;
  headers: Record<string, string>;
}

function UploadDialog({
  open,
  onOpenChange,
  initial,
  onBusyChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: UploadOptions;
  onBusyChange: (busy: boolean) => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const courses = useCourses();
  const [file, setFile] = useState<File | null>(initial.file ?? null);
  const [title, setTitle] = useState(initial.file ? titleFromFilename(initial.file.name) : "");
  const [titleEdited, setTitleEdited] = useState(false);
  const [courseId, setCourseId] = useState<string>(initial.courseId ?? "none");
  const [licence, setLicence] = useState("");
  const [attribution, setAttribution] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "edit" });
  const request = useRef<XMLHttpRequest | null>(null);
  const busy = phase.kind === "uploading" || phase.kind === "starting";

  useEffect(() => onBusyChange(busy), [busy, onBusyChange]);

  function choose(next: File | null) {
    setFile(next);
    if (next && !titleEdited) setTitle(titleFromFilename(next.name));
    if (phase.kind === "error") setPhase({ kind: "edit" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    try {
      setPhase({ kind: "uploading", loaded: 0, total: file.size, rate: 0 });
      const created = unwrap(
        await api.POST("/v1/lectures", {
          body: {
            title: title.trim() || titleFromFilename(file.name),
            filename: file.name,
            content_type: file.type || "video/mp4",
            course_id: courseId === "none" ? null : courseId,
            licence: licence.trim() || null,
            attribution: attribution.trim() || null,
          },
        }),
      );
      await put(created.upload, file, request, (loaded, rate) =>
        setPhase({ kind: "uploading", loaded, total: file.size, rate }),
      );
      const params = { params: { path: { lecture_id: created.lecture.id } } };
      unwrap(await api.POST("/v1/lectures/{lecture_id}/complete-upload", params));
      setPhase({ kind: "starting" });
      unwrap(await api.POST("/v1/lectures/{lecture_id}/process", params));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["lectures"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
      ]);
      toast.success("Uploaded. Processing has started.", { description: created.lecture.title });
      setPhase({ kind: "edit" });
      onOpenChange(false);
      router.push(`/lectures/${created.lecture.id}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setPhase(message === "cancelled" ? { kind: "edit" } : { kind: "error", message });
    }
  }

  const keepOpen = (event: Event) => {
    if (busy) event.preventDefault();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (busy && !next ? undefined : onOpenChange(next))}>
      <DialogContent
        className="gap-5 sm:max-w-lg"
        showCloseButton={!busy}
        onInteractOutside={keepOpen}
        onEscapeKeyDown={keepOpen}
      >
        <DialogHeader>
          <DialogTitle>Add a lecture</DialogTitle>
          <DialogDescription>
            Upload a lecture video. It&apos;s transcribed and its slides are read, then you get notes, a quiz, search
            and Q&amp;A.
          </DialogDescription>
        </DialogHeader>

        <form id="upload" onSubmit={submit} className="flex flex-col gap-4">
          {file ? (
            <ChosenFile file={file} onClear={busy ? undefined : () => choose(null)} />
          ) : (
            <label className="group flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed bg-muted/30 bg-dots px-6 py-10 text-center transition-colors hover:border-primary/60 hover:bg-brand-soft/40 focus-within:border-primary">
              <span className="flex size-11 items-center justify-center rounded-full bg-brand-soft text-brand-ink transition-transform group-hover:scale-105">
                <CloudUploadIcon className="size-5" />
              </span>
              <span className="text-sm font-medium">Drop a video here, or click to choose one</span>
              <span className="text-xs text-muted-foreground">MP4, WebM or MOV. Slides with spoken explanations work best.</span>
              <input
                type="file"
                accept="video/*"
                className="sr-only"
                onChange={(event) => choose(event.target.files?.[0] ?? null)}
              />
            </label>
          )}

          <div className="grid gap-2">
            <Label htmlFor="upload-title">Title</Label>
            <Input
              id="upload-title"
              value={title}
              onChange={(event) => {
                setTitle(event.target.value);
                setTitleEdited(true);
              }}
              placeholder="Taken from the file name"
              maxLength={300}
              disabled={busy}
            />
          </div>

          {courses.data && courses.data.length > 0 && (
            <div className="grid gap-2">
              <Label htmlFor="upload-course">Course</Label>
              <Select value={courseId} onValueChange={setCourseId} disabled={busy}>
                <SelectTrigger id="upload-course" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No course</SelectItem>
                  {courses.data.map((course) => (
                    <SelectItem key={course.id} value={course.id}>
                      {course.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <Collapsible>
            <CollapsibleTrigger className="group flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
              <ChevronDownIcon className="size-4 transition-transform group-data-[state=closed]:-rotate-90" />
              Source and licence
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="upload-licence">Licence</Label>
                <Input
                  id="upload-licence"
                  value={licence}
                  onChange={(event) => setLicence(event.target.value)}
                  placeholder="CC BY-NC-SA 4.0"
                  maxLength={100}
                  disabled={busy}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="upload-attribution">Attribution</Label>
                <Input
                  id="upload-attribution"
                  value={attribution}
                  onChange={(event) => setAttribution(event.target.value)}
                  placeholder="MIT OpenCourseWare"
                  maxLength={2000}
                  disabled={busy}
                />
              </div>
            </CollapsibleContent>
          </Collapsible>

          {phase.kind === "uploading" && (
            <div className="flex flex-col gap-2" role="status">
              <Progress value={(phase.loaded / Math.max(phase.total, 1)) * 100} />
              <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
                <span>
                  Uploading {formatBytes(phase.loaded)} of {formatBytes(phase.total)}
                </span>
                <span>
                  {phase.rate > 0
                    ? `${formatBytes(phase.rate)}/s · ${formatDuration((phase.total - phase.loaded) / phase.rate)} left`
                    : "Starting…"}
                </span>
              </div>
            </div>
          )}
          {phase.kind === "starting" && (
            <p className="text-sm text-muted-foreground" role="status">
              Uploaded. Starting processing…
            </p>
          )}
          {phase.kind === "error" && (
            <Callout tone="error" title="The upload didn't finish">
              {phase.message}
            </Callout>
          )}
        </form>

        <DialogFooter>
          {phase.kind === "uploading" ? (
            <Button variant="outline" onClick={() => request.current?.abort()}>
              Cancel upload
            </Button>
          ) : (
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              Close
            </Button>
          )}
          <Button type="submit" form="upload" disabled={!file || busy}>
            <CloudUploadIcon />
            {busy ? "Uploading…" : "Upload and process"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The chosen file, with a frame from it and its length, read locally before uploading. */
function ChosenFile({ file, onClear }: { file: File; onClear?: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return (
    <div className="flex items-center gap-3 rounded-xl border bg-muted/30 p-2.5">
      <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-lg bg-black">
        {url && (
          <video
            src={`${url}#t=1`}
            muted
            preload="metadata"
            className="size-full object-cover"
            onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
          />
        )}
        {duration !== null && Number.isFinite(duration) && (
          <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 font-mono text-[0.65rem] text-white">
            {formatTime(duration)}
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 truncate text-sm font-medium">
          <FileVideoIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{file.name}</span>
        </p>
        <p className="text-xs text-muted-foreground">
          {formatBytes(file.size)}
          {file.type && ` · ${file.type}`}
        </p>
      </div>
      {onClear && (
        <Button type="button" variant="ghost" size="icon-sm" onClick={onClear} aria-label="Choose another file">
          <XIcon />
        </Button>
      )}
    </div>
  );
}

/** PUT with upload progress, which fetch can't report. Headers must match what the URL was
 *  signed with. Aborting rejects with "cancelled". */
function put(
  target: UploadTarget,
  file: File,
  handle: { current: XMLHttpRequest | null },
  onProgress: (loaded: number, bytesPerSecond: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    handle.current = request;
    const started = performance.now();
    request.open(target.method, target.url);
    for (const [name, value] of Object.entries(target.headers)) request.setRequestHeader(name, value);
    request.upload.onprogress = (event) => {
      const seconds = (performance.now() - started) / 1000;
      if (event.lengthComputable) onProgress(event.loaded, seconds > 0.5 ? event.loaded / seconds : 0);
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(new Error(`Storage refused the upload (HTTP ${request.status}).`));
    request.onerror = () => reject(new Error("Couldn't reach storage."));
    request.onabort = () => reject(new Error("cancelled"));
    request.send(file);
  });
}

/** The dashed drop target on empty pages, which opens the upload dialog. */
export function DropTarget({ className, courseId }: { className?: string; courseId?: string }) {
  const { openUpload } = useUpload();
  return (
    <button
      type="button"
      onClick={() => openUpload({ courseId })}
      className={cn(
        "group flex w-full flex-col items-center gap-3 rounded-2xl border-2 border-dashed bg-card bg-dots px-6 py-14 text-center transition-colors hover:border-primary/60",
        className,
      )}
    >
      <span className="flex size-12 items-center justify-center rounded-full bg-brand-soft text-brand-ink transition-transform group-hover:scale-105">
        <CloudUploadIcon className="size-6" />
      </span>
      <span className="text-base font-semibold">Add a lecture</span>
      <span className="max-w-sm text-sm text-muted-foreground">
        Drop a video anywhere on this page, or click to choose one.
      </span>
    </button>
  );
}
