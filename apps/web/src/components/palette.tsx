"use client";

import { useQuery } from "@tanstack/react-query";
import {
  CloudUploadIcon,
  FilmIcon,
  LibraryIcon,
  MonitorIcon,
  MoonIcon,
  SearchIcon,
  SunIcon,
  TextSearchIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, createContext, use, useEffect, useState } from "react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import { api, unwrap } from "@/lib/api";
import { pluralise } from "@/lib/format";
import { useCourses, useLectures } from "@/lib/queries";
import { lectureHref } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { Highlighted } from "./search";
import { useTheme } from "./theme";
import { useUpload } from "./upload";

const PaletteContext = createContext<{ openPalette: () => void }>({ openPalette: () => {} });

export function usePalette() {
  return use(PaletteContext);
}

/** Ctrl+K / ⌘K anywhere: go to a lecture or course, or search inside every lecture. */
export function PaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <PaletteContext value={{ openPalette: () => setOpen(true) }}>
      {children}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="top-[12vh] translate-y-0 overflow-hidden p-0 sm:max-w-2xl" showCloseButton={false}>
          <DialogHeader className="sr-only">
            <DialogTitle>Search the library</DialogTitle>
            <DialogDescription>Go to a lecture or course, or search inside the lectures.</DialogDescription>
          </DialogHeader>
          {open && <Palette close={() => setOpen(false)} />}
        </DialogContent>
      </Dialog>
    </PaletteContext>
  );
}

/** Content search waits for a pause in typing, since each query embeds and reranks. */
function useDebounced(value: string, ms: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

function Palette({ close }: { close: () => void }) {
  const router = useRouter();
  const { openUpload } = useUpload();
  const { setTheme } = useTheme();
  const lectures = useLectures();
  const courses = useCourses();
  const [input, setInput] = useState("");
  const query = useDebounced(input.trim(), 400);
  const searchable = query.length >= 3;
  const search = useQuery({
    queryKey: ["search", "library", query],
    queryFn: async () => unwrap(await api.GET("/v1/search", { params: { query: { q: query, limit: 8 } } })),
    enabled: searchable,
    staleTime: Infinity,
  });

  const needle = input.trim().toLowerCase();
  const matchingLectures = (lectures.data ?? []).filter((l) => l.title.toLowerCase().includes(needle)).slice(0, 6);
  const matchingCourses = (courses.data ?? []).filter((c) => c.title.toLowerCase().includes(needle)).slice(0, 4);
  const titles = new Map((lectures.data ?? []).map((l) => [l.id, l.title]));
  const go = (href: string) => {
    close();
    router.push(href);
  };
  const waiting = searchable && (search.isFetching || input.trim() !== query);

  return (
    <Command shouldFilter={false} loop className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:text-xs">
      <CommandInput
        value={input}
        onValueChange={setInput}
        placeholder="Search lectures, courses, or what was said…"
        className="h-12 text-base"
      />
      <CommandList className="max-h-[min(60vh,32rem)]">
        {!waiting && <CommandEmpty>Nothing matches “{input.trim()}”.</CommandEmpty>}

        {matchingLectures.length > 0 && (
          <CommandGroup heading="Lectures">
            {matchingLectures.map((lecture) => (
              <CommandItem key={lecture.id} value={`lecture-${lecture.id}`} onSelect={() => go(lectureHref(lecture.id))}>
                <FilmIcon />
                <span className="truncate">{lecture.title}</span>
                {lecture.duration_s != null && <CommandShortcut>{formatTime(lecture.duration_s)}</CommandShortcut>}
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {matchingCourses.length > 0 && (
          <CommandGroup heading="Courses">
            {matchingCourses.map((course) => (
              <CommandItem key={course.id} value={`course-${course.id}`} onSelect={() => go(`/courses/${course.id}`)}>
                <LibraryIcon />
                <span className="truncate">{course.title}</span>
                <CommandShortcut>{pluralise(course.lecture_count, "lecture")}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {searchable && (
          <CommandGroup heading="Said or shown in lectures">
            {waiting && (
              <div className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
                <Spinner /> Searching inside the lectures…
              </div>
            )}
            {search.isError && !waiting && (
              <p className="px-2 py-3 text-sm text-destructive">{search.error.message}</p>
            )}
            {!waiting &&
              search.data?.hits.map((hit) => (
                <CommandItem
                  key={`${hit.lecture_id}-${hit.segment_id}`}
                  value={`hit-${hit.lecture_id}-${hit.segment_id}`}
                  onSelect={() => go(lectureHref(hit.lecture_id, hit.start_s))}
                  className="items-start"
                >
                  <TextSearchIcon className="mt-0.5" />
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex items-baseline gap-2 text-xs text-muted-foreground">
                      <span className="font-mono text-brand-ink">{formatTime(hit.start_s)}</span>
                      <span className="truncate">
                        {[titles.get(hit.lecture_id), hit.slide_title ?? hit.chapter].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span className="line-clamp-2 text-sm">
                      <Highlighted text={hit.transcript} query={query} />
                    </span>
                  </div>
                </CommandItem>
              ))}
          </CommandGroup>
        )}

        {!needle && (
          <CommandGroup heading="Actions">
            <CommandItem
              value="upload"
              onSelect={() => {
                close();
                openUpload();
              }}
            >
              <CloudUploadIcon /> Add a lecture
            </CommandItem>
            <CommandItem value="library" onSelect={() => go("/")}>
              <LibraryIcon /> Go to the library
            </CommandItem>
            <CommandItem value="theme-light" onSelect={() => setTheme("light")}>
              <SunIcon /> Light theme
            </CommandItem>
            <CommandItem value="theme-dark" onSelect={() => setTheme("dark")}>
              <MoonIcon /> Dark theme
            </CommandItem>
            <CommandItem value="theme-system" onSelect={() => setTheme("system")}>
              <MonitorIcon /> System theme
            </CommandItem>
          </CommandGroup>
        )}
      </CommandList>
      <div className="flex items-center gap-3 border-t px-3 py-2 text-xs text-muted-foreground">
        <SearchIcon className="size-3.5" />
        <span className="flex-1">
          {searchable
            ? "Inside the lectures, matched by meaning as well as words."
            : "Type 3 or more letters to search inside the lectures."}
        </span>
        <span className="hidden items-center gap-1 sm:flex">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> to move <Kbd>↵</Kbd> to open
        </span>
      </div>
    </Command>
  );
}
