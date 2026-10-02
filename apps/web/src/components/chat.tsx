"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  ArrowUpIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  EllipsisIcon,
  HistoryIcon,
  MessagesSquareIcon,
  PlusIcon,
  SparklesIcon,
  SquareIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  Trash2Icon,
} from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { allowance, refusal, resetText } from "@/lib/access";
import { type ChatMessage, type Citation, type Rating, type Source, api, ensureOk, unwrap } from "@/lib/api";
import { type Inline, parseAnswer, resolveCitation } from "@/lib/answer";
import { askQuestion } from "@/lib/ask";
import { formatCost, formatRelative, pluralise } from "@/lib/format";
import { meKey, scopeKey, threadKey, useThread, useThreads } from "@/lib/queries";
import type { Open, Scope } from "@/lib/scope";
import { formatTime } from "@/lib/timeline";

import { SignInPrompt, useAccount } from "./account";
import { Callout, Latex, TimeChip, loadKatex } from "./common";

/** The answer being streamed, until it's saved and the thread reloads. */
interface Pending {
  question: string;
  questionId: string | null;
  text: string;
  sources: Source[] | null;
}

/** The Ask tab: the chat once signed in; with sign-in on at the API, visitors get a prompt to
 *  sign in instead, since asking needs an account (conversations are private to their user). */
export function AskPanel(props: { scope: Scope; onOpen: Open; suggestions?: string[] }) {
  const account = useAccount();
  const where = props.scope.kind === "lecture" ? "this lecture" : "this course";
  // Answers may hold formulas: have KaTeX ready by the time one arrives.
  useEffect(() => void loadKatex(), []);
  if (account.pending) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-24" />
      </div>
    );
  }
  if (account.auth && !account.signedIn) {
    return (
      <div className="flex h-full items-center justify-center">
        <SignInPrompt title="Sign in to ask questions">
          Answers come only from {where}, and each point links to the moment it comes from. Reading and
          search work without an account.
        </SignInPrompt>
      </div>
    );
  }
  return <ChatPanel {...props} />;
}

/** Q&A about a lecture, or across a course's lectures. Citations play the lecture they point
 *  into from where they point. `suggestions` are questions to start from. */
const ChatPanel = memo(function ChatPanel({
  scope,
  onOpen,
  suggestions = [],
}: {
  scope: Scope;
  onOpen: Open;
  suggestions?: string[];
}) {
  const queryClient = useQueryClient();
  const account = useAccount();
  const threads = useThreads(scope);
  const where = scope.kind === "lecture" ? "this lecture" : "this course";
  // undefined until chosen: then the most recent thread, if any.
  const [chosen, setChosen] = useState<string | null | undefined>(undefined);
  const threadId = chosen === undefined ? (threads.data?.[0]?.id ?? null) : chosen;
  const thread = useThread(threadId);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const abort = useRef<AbortController | null>(null);
  // Follow the answer as it streams, unless the reader has scrolled up to read.
  const stick = useRef(true);
  // Signed-in users (not admins) have a daily allowance, and everyone waits when the day's
  // shared budget is spent.
  const quotas = account.quotas;
  const left = quotas ? allowance(quotas).questions : null;
  const limit = quotas?.paused
    ? `Today's shared budget for the language model is spent. Questions resume at ${resetText(quotas.resets_at)}.`
    : quotas && left === 0
      ? `You've asked your ${quotas.questions_per_day} questions for today. More at ${resetText(quotas.resets_at)}.`
      : null;

  const messageCount = thread.data?.messages.length ?? 0;
  useEffect(() => {
    const box = scroller.current;
    if (box && stick.current) box.scrollTop = box.scrollHeight;
  }, [pending?.text, pending?.sources, messageCount, threadId]);

  useEffect(() => {
    const box = input.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight}px`;
  }, [draft]);

  async function send(text = draft) {
    const question = text.trim();
    if (!question || pending || limit) return;
    setDraft("");
    setError(null);
    stick.current = true;
    setPending({ question, questionId: null, text: "", sources: null });
    const controller = new AbortController();
    abort.current = controller;
    let asked = threadId;
    try {
      await askQuestion(
        scope,
        question,
        threadId,
        (event) => {
          if (event.type === "start") {
            asked = event.thread_id;
            setChosen(event.thread_id);
            setPending((p) => p && { ...p, questionId: event.question.id });
          } else if (event.type === "sources") {
            setPending((p) => p && { ...p, sources: event.sources });
          } else if (event.type === "delta") {
            setPending((p) => p && { ...p, text: p.text + event.text });
          }
          // done and error: the saved answer, error included, comes with the thread below.
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) setError(refusal(e));
    }
    abort.current = null;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: threadKey(asked) }),
      queryClient.invalidateQueries({ queryKey: scopeKey(scope, "threads") }),
      queryClient.invalidateQueries({ queryKey: meKey }),
    ]);
    setPending(null);
  }

  async function remove() {
    if (!threadId) return;
    try {
      ensureOk(await api.DELETE("/v1/threads/{thread_id}", { params: { path: { thread_id: threadId } } }));
      setChosen(null);
      await queryClient.invalidateQueries({ queryKey: scopeKey(scope, "threads") });
      toast.success("Conversation deleted");
    } catch (e) {
      toast.error("Couldn't delete the conversation", { description: refusal(e) });
    }
  }

  // While an answer streams, its question is shown with it, not from the reloaded thread.
  const messages = (thread.data?.messages ?? []).filter((m) => m.id !== pending?.questionId);
  const current = threads.data?.find((t) => t.id === threadId);
  const empty = messages.length === 0 && !pending;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b px-2 py-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="min-w-0 shrink justify-start" disabled={pending !== null}>
              <HistoryIcon />
              <span className="truncate">{current?.title ?? "New conversation"}</span>
              <ChevronDownIcon className="text-muted-foreground" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-72">
            <DropdownMenuLabel>Conversations</DropdownMenuLabel>
            {threads.data && threads.data.length > 0 ? (
              <DropdownMenuRadioGroup value={threadId ?? ""} onValueChange={(value) => setChosen(value || null)}>
                {threads.data.map((t) => (
                  <DropdownMenuRadioItem key={t.id} value={t.id} className="flex-col items-start gap-0">
                    <span className="line-clamp-1">{t.title}</span>
                    <span className="text-xs text-muted-foreground">{formatRelative(t.updated_at)}</span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            ) : (
              <p className="px-2 py-1.5 text-sm text-muted-foreground">None yet.</p>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        <div className="ml-auto flex shrink-0 items-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => {
                  setChosen(null);
                  input.current?.focus();
                }}
                disabled={pending !== null || threadId === null}
                aria-label="New conversation"
              >
                <PlusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>New conversation</TooltipContent>
          </Tooltip>
          {threadId && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Conversation actions" disabled={pending !== null}>
                  <EllipsisIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
                  <Trash2Icon /> Delete conversation
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-5"
        onScroll={(event) => {
          const box = event.currentTarget;
          stick.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        }}
      >
        {empty ? (
          <Intro where={where} suggestions={suggestions} onPick={(s) => void send(s)} />
        ) : (
          <ol className="flex flex-col gap-6" aria-label="Messages">
            {messages.map((message) =>
              message.role === "user" ? (
                <Question key={message.id} text={message.content} />
              ) : (
                <Answer key={message.id}>
                  <AnswerText
                    text={message.content}
                    citations={message.citations ?? null}
                    sources={message.sources ?? null}
                    scope={scope}
                    onOpen={onOpen}
                  />
                  {message.error && <Callout tone="error">{message.error}</Callout>}
                  <Sources sources={message.sources ?? []} onOpen={onOpen} />
                  {threadId && !message.error && <AnswerFooter message={message} threadId={threadId} />}
                </Answer>
              ),
            )}
            {pending && (
              <>
                <Question text={pending.question} />
                <Answer live>
                  {pending.text ? (
                    <AnswerText
                      text={pending.text}
                      citations={null}
                      sources={pending.sources}
                      scope={scope}
                      onOpen={onOpen}
                      streaming
                    />
                  ) : (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span className="flex gap-1">
                        <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                        <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                        <span className="size-1.5 animate-bounce rounded-full bg-current" />
                      </span>
                      {pending.sources ? `Read ${pluralise(pending.sources.length, "passage")}, writing…` : `Searching ${where}…`}
                    </p>
                  )}
                </Answer>
              </>
            )}
          </ol>
        )}
        {error && (
          <Callout tone="error" title="The question wasn't answered" className="mt-4">
            {error}
          </Callout>
        )}
      </div>

      <form
        className="flex flex-col gap-2 border-t p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {limit && <Callout tone="warning">{limit}</Callout>}
        <div className="flex items-end gap-2 rounded-xl border bg-background p-1.5 shadow-xs transition focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50">
          <textarea
            ref={input}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void send();
              }
            }}
            rows={1}
            maxLength={1000}
            disabled={limit !== null}
            placeholder={threadId ? "Ask a follow-up" : `Ask about ${where}`}
            aria-label="Your question"
            className="max-h-40 min-h-9 min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          {pending ? (
            <Button
              type="button"
              size="icon-sm"
              variant="secondary"
              onClick={() => abort.current?.abort()}
              aria-label="Stop the answer"
            >
              <SquareIcon className="fill-current" />
            </Button>
          ) : (
            <Button type="submit" size="icon-sm" disabled={!draft.trim() || limit !== null} aria-label="Ask">
              <ArrowUpIcon />
            </Button>
          )}
        </div>
        <p className="px-1 text-[0.7rem] text-muted-foreground">
          Answers come only from {where}.
          {left !== null && ` ${pluralise(left, "question")} left today.`} Enter to send, Shift+Enter for a new
          line.
        </p>
      </form>

      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{current?.title}” and its answers and ratings are deleted. This can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void remove()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
});

function Intro({ where, suggestions, onPick }: { where: string; suggestions: string[]; onPick: (s: string) => void }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-5 py-4 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-brand-soft text-brand-ink">
        <MessagesSquareIcon className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="font-semibold">Ask about {where}</p>
        <p className="max-w-xs text-sm text-balance text-muted-foreground">
          Answers come only from {where}, and each point links to the moment it comes from.
        </p>
      </div>
      {suggestions.length > 0 && (
        <div className="flex w-full flex-col gap-2">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onPick(suggestion)}
              className="rounded-lg border bg-background px-3 py-2 text-left text-sm transition-colors hover:border-primary/40 hover:bg-brand-soft/40"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <li className="flex justify-end">
      <p className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
        {text}
      </p>
    </li>
  );
}

function Answer({ children, live = false }: { children: React.ReactNode; live?: boolean }) {
  return (
    <li className="flex gap-3" aria-live={live ? "polite" : undefined}>
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-ink">
        <SparklesIcon className="size-3.5" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-3">{children}</div>
    </li>
  );
}

/** An answer with clickable citations. One pointing outside what was retrieved is struck
 *  through and not clickable. */
function AnswerText({
  text,
  citations,
  sources,
  scope,
  onOpen,
  streaming = false,
}: {
  text: string;
  citations: Citation[] | null;
  sources: Source[] | null;
  scope: Scope;
  onOpen: Open;
  streaming?: boolean;
}) {
  const renderInlines = (inlines: Inline[]) =>
    inlines.map((inline, i) => {
      switch (inline.kind) {
        case "text":
          return <span key={i}>{inline.text}</span>;
        case "bold":
          return <strong key={i} className="font-semibold">{renderInlines(inline.children)}</strong>;
        case "code":
          return (
            <code key={i} className="rounded bg-muted px-1 py-px font-mono text-[0.85em]">
              {inline.text}
            </code>
          );
        case "math":
          return <Latex key={i} source={inline.tex} inline />;
        case "cite": {
          const { lectureId, valid, title } = resolveCitation(inline, citations, sources, scope);
          if (valid && lectureId) {
            return (
              <TimeChip
                key={i}
                seconds={inline.seconds}
                label={inline.label}
                lecture={scope.kind === "course" ? title : null}
                onSeek={(seconds) => onOpen(lectureId, seconds)}
                className="mx-0.5"
              />
            );
          }
          return (
            <span
              key={i}
              className={cn(
                "mx-0.5 font-mono text-[0.8em]",
                valid === false ? "text-destructive line-through" : "text-muted-foreground",
              )}
              title={valid === false ? "This time isn't in the passages the answer was given" : undefined}
            >
              {inline.label.replace(/^\[|\]$/g, "")}
            </span>
          );
        }
      }
    });

  const blocks = parseAnswer(text);
  return (
    <div className="flex flex-col gap-2.5 text-sm leading-relaxed">
      {blocks.map((block, i) => {
        const caret = streaming && i === blocks.length - 1 && (
          <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-primary/70" aria-hidden />
        );
        return block.kind === "paragraph" ? (
          <p key={i}>
            {renderInlines(block.inlines)}
            {caret}
          </p>
        ) : block.ordered ? (
          <ol key={i} className="flex list-decimal flex-col gap-1 pl-5 marker:text-muted-foreground">
            {block.items.map((item, j) => (
              <li key={j}>{renderInlines(item)}</li>
            ))}
            {caret}
          </ol>
        ) : (
          <ul key={i} className="flex list-disc flex-col gap-1 pl-5 marker:text-muted-foreground">
            {block.items.map((item, j) => (
              <li key={j}>{renderInlines(item)}</li>
            ))}
            {caret}
          </ul>
        );
      })}
    </div>
  );
}

function Sources({ sources, onOpen }: { sources: Source[]; onOpen: Open }) {
  if (sources.length === 0) return null;
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronDownIcon className="size-3.5 transition-transform group-data-[state=closed]:-rotate-90" />
        Based on {pluralise(sources.length, "passage")}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-2 flex flex-col gap-1.5 border-l pl-3">
          {[...sources]
            .sort((a, b) => (a.lecture_label ?? "").localeCompare(b.lecture_label ?? "") || a.start_s - b.start_s)
            .map((source) => (
              <li key={`${source.lecture_id}-${source.segment_id}`} className="flex items-baseline gap-2 text-xs">
                <TimeChip
                  seconds={source.start_s}
                  label={source.lecture_label ? `${source.lecture_label} ${formatTime(source.start_s)}` : undefined}
                  lecture={source.lecture_label ? source.lecture_title : null}
                  onSeek={(seconds) => onOpen(source.lecture_id, seconds)}
                />
                <span className="min-w-0 text-muted-foreground">
                  {source.lecture_label && source.lecture_title && `${source.lecture_title} · `}
                  {source.slide_title ?? source.chapter ?? `until ${formatTime(source.end_s)}`}
                </span>
              </li>
            ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** Copy, rate, and what the answer took: model, time to first word, total time and cost. */
function AnswerFooter({ message, threadId }: { message: ChatMessage; threadId: string }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const rating = message.feedback?.rating ?? null;

  async function rate(value: Rating, why: string | null) {
    try {
      unwrap(await api.POST("/v1/feedback", { body: { message_id: message.id, rating: value, reason: why } }));
      await queryClient.invalidateQueries({ queryKey: threadKey(threadId) });
      toast.success(value === "up" ? "Thanks, marked helpful" : "Thanks, noted what went wrong");
    } catch (e) {
      toast.error("Couldn't save the rating", { description: refusal(e) });
    }
  }

  const model = message.model?.replace(/^[a-z-]+:/, "");
  const details = [
    model,
    message.first_token_ms != null && `first words in ${(message.first_token_ms / 1000).toFixed(1)} s`,
    message.total_ms != null && `done in ${(message.total_ms / 1000).toFixed(1)} s`,
    message.usage && `${message.usage.input_tokens.toLocaleString()} tokens in, ${message.usage.output_tokens.toLocaleString()} out`,
    message.cost_usd != null && `${formatCost(message.cost_usd)} at paid rates`,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-2">
      <div className="-ml-1.5 flex items-center gap-0.5 text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label="Copy the answer"
              onClick={() => {
                void navigator.clipboard.writeText(message.content);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>Copy</TooltipContent>
        </Tooltip>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Helpful"
          aria-pressed={rating === "up"}
          className="aria-pressed:text-success"
          onClick={() => void rate("up", null)}
        >
          <ThumbsUpIcon className={cn(rating === "up" && "fill-current")} />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Not helpful"
          aria-pressed={rating === "down"}
          className="aria-pressed:text-destructive"
          onClick={() => setAsking((open) => !open)}
        >
          <ThumbsDownIcon className={cn(rating === "down" && "fill-current")} />
        </Button>
        {details.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="ml-auto cursor-default truncate pl-2 text-[0.7rem] tabular-nums">
                {[model, message.total_ms != null && `${(message.total_ms / 1000).toFixed(1)} s`, message.cost_usd != null && formatCost(message.cost_usd)]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </TooltipTrigger>
            <TooltipContent className="flex flex-col gap-0.5">
              {details.map((line) => (
                <span key={String(line)}>{line}</span>
              ))}
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      {asking && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setAsking(false);
            void rate("down", reason.trim() || null);
          }}
        >
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="What was wrong? (optional)"
            aria-label="What was wrong with this answer"
            maxLength={2000}
            autoFocus
            className="h-8 text-sm"
          />
          <Button type="submit" size="sm" variant="secondary">
            Send
          </Button>
        </form>
      )}
    </div>
  );
}
