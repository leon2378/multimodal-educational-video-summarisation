"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { type ChatMessage, type Citation, type Rating, type Source, api, unwrap } from "@/lib/api";
import { type Inline, parseAnswer } from "@/lib/answer";
import { askQuestion } from "@/lib/ask";
import { lectureKey, threadKey, useThread, useThreads } from "@/lib/queries";
import { formatTime } from "@/lib/timeline";

import { Latex, TimeButton } from "./ui";

type Seek = (seconds: number) => void;

/** The answer being streamed, until it's saved and the thread reloads. */
interface Pending {
  question: string;
  questionId: string | null;
  text: string;
  sources: Source[] | null;
}

export function ChatPanel({ lectureId, onSeek }: { lectureId: string; onSeek: Seek }) {
  const queryClient = useQueryClient();
  const threads = useThreads(lectureId);
  // undefined until chosen: then the most recent thread, if any.
  const [chosen, setChosen] = useState<string | null | undefined>(undefined);
  const threadId = chosen === undefined ? (threads.data?.[0]?.id ?? null) : chosen;
  const thread = useThread(threadId);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const messageCount = thread.data?.messages.length ?? 0;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [pending?.text, messageCount]);

  async function send() {
    const question = draft.trim();
    if (!question || pending) return;
    setDraft("");
    setError(null);
    setPending({ question, questionId: null, text: "", sources: null });
    let asked = threadId;
    try {
      await askQuestion(lectureId, question, threadId, (event) => {
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
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: threadKey(asked) }),
      queryClient.invalidateQueries({ queryKey: lectureKey(lectureId, "threads") }),
    ]);
    setPending(null);
  }

  // While an answer streams, its question is shown with it, not from the reloaded thread.
  const messages = (thread.data?.messages ?? []).filter((m) => m.id !== pending?.questionId);

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex items-center gap-2">
        {threads.data && threads.data.length > 0 && (
          <select
            value={threadId ?? ""}
            onChange={(event) => setChosen(event.target.value || null)}
            disabled={pending !== null}
            aria-label="Conversation"
            className="min-w-0 flex-1 truncate rounded-lg border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-950"
          >
            <option value="">New conversation</option>
            {threads.data.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={() => setChosen(null)}
          disabled={pending !== null || threadId === null}
          className="ml-auto rounded-lg border border-slate-300 px-2 py-1 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          New chat
        </button>
      </div>

      {messages.length === 0 && !pending && (
        <p className="text-slate-500">
          Ask anything about this lecture. Answers come only from the lecture, and each point links to the moment it
          comes from.
        </p>
      )}
      <ol className="flex flex-col gap-4" aria-label="Messages">
        {messages.map((message) =>
          message.role === "user" ? (
            <Question key={message.id} text={message.content} />
          ) : (
            <li key={message.id} className="flex flex-col gap-2">
              <AnswerText
                text={message.content}
                citations={message.citations ?? null}
                sources={message.sources ?? null}
                onSeek={onSeek}
              />
              {message.error && (
                <p className="text-rose-600" role="alert">
                  {message.error}
                </p>
              )}
              <Sources sources={message.sources ?? []} onSeek={onSeek} />
              {threadId && !message.error && <Rate message={message} threadId={threadId} />}
            </li>
          ),
        )}
        {pending && (
          <>
            <Question text={pending.question} />
            <li className="flex flex-col gap-2" aria-live="polite">
              {pending.text ? (
                <AnswerText text={pending.text} citations={null} sources={pending.sources} onSeek={onSeek} />
              ) : (
                <p className="animate-pulse text-slate-500">{pending.sources ? "Writing…" : "Searching the lecture…"}</p>
              )}
            </li>
          </>
        )}
      </ol>
      {error && (
        <p className="text-rose-600" role="alert">
          {error}
        </p>
      )}
      <div ref={bottom} />

      <form
        className="sticky bottom-0 flex items-end gap-2 bg-white pt-2 dark:bg-slate-900"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          rows={2}
          maxLength={1000}
          placeholder={threadId ? "Ask a follow-up" : "Ask about this lecture"}
          aria-label="Your question"
          className="min-w-0 flex-1 resize-none rounded-lg border border-slate-300 px-3 py-1.5 dark:border-slate-700 dark:bg-slate-950"
        />
        <button
          type="submit"
          disabled={!draft.trim() || pending !== null}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Ask
        </button>
      </form>
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <li className="self-end rounded-lg bg-indigo-50 px-3 py-2 whitespace-pre-wrap dark:bg-indigo-950">{text}</li>
  );
}

/** An answer with its citations checked: by the server once saved, and against the retrieved
 *  segments while streaming (or for a time the server didn't list). A citation outside them is
 *  struck through and not clickable. */
function AnswerText({
  text,
  citations,
  sources,
  onSeek,
}: {
  text: string;
  citations: Citation[] | null;
  sources: Source[] | null;
  onSeek: Seek;
}) {
  const isGrounded = (label: string, seconds: number): boolean | null => {
    const checked = citations?.find((c) => c.label === label);
    if (checked) return checked.valid;
    if (sources) return sources.some((s) => s.start_s - 1 <= seconds && seconds <= s.end_s + 1);
    return null;
  };
  const renderInlines = (inlines: Inline[]) =>
    inlines.map((inline, i) => {
      switch (inline.kind) {
        case "text":
          return <span key={i}>{inline.text}</span>;
        case "bold":
          return <strong key={i}>{renderInlines(inline.children)}</strong>;
        case "code":
          return (
            <code key={i} className="rounded bg-slate-100 px-1 font-mono text-[0.9em] dark:bg-slate-800">
              {inline.text}
            </code>
          );
        case "math":
          return <Latex key={i} source={inline.tex} inline />;
        case "cite": {
          const grounded = isGrounded(inline.label, inline.seconds);
          if (grounded) return <TimeButton key={i} seconds={inline.seconds} onSeek={onSeek} />;
          return (
            <span
              key={i}
              className={`font-mono text-sm ${grounded === false ? "text-rose-600 line-through" : "text-slate-500"}`}
              title={grounded === false ? "This time isn't in the passages the answer was given" : undefined}
            >
              {inline.label}
            </span>
          );
        }
      }
    });

  return (
    <div className="flex flex-col gap-2 leading-relaxed">
      {parseAnswer(text).map((block, i) =>
        block.kind === "paragraph" ? (
          <p key={i}>{renderInlines(block.inlines)}</p>
        ) : block.ordered ? (
          <ol key={i} className="list-decimal pl-5">
            {block.items.map((item, j) => (
              <li key={j}>{renderInlines(item)}</li>
            ))}
          </ol>
        ) : (
          <ul key={i} className="list-disc pl-5">
            {block.items.map((item, j) => (
              <li key={j}>{renderInlines(item)}</li>
            ))}
          </ul>
        ),
      )}
    </div>
  );
}

function Sources({ sources, onSeek }: { sources: Source[]; onSeek: Seek }) {
  if (sources.length === 0) return null;
  return (
    <details className="text-xs text-slate-500">
      <summary className="cursor-pointer">Searched {sources.length} passages</summary>
      <ul className="mt-1 flex flex-col gap-1">
        {[...sources]
          .sort((a, b) => a.start_s - b.start_s)
          .map((source) => (
            <li key={`${source.lecture_id}-${source.segment_id}`}>
              <TimeButton seconds={source.start_s} onSeek={onSeek} />
              {source.slide_title ?? source.chapter ?? `until ${formatTime(source.end_s)}`}
            </li>
          ))}
      </ul>
    </details>
  );
}

function Rate({ message, threadId }: { message: ChatMessage; threadId: string }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const current = message.feedback?.rating ?? null;

  async function rate(rating: Rating, why: string | null) {
    setError(null);
    try {
      unwrap(await api.POST("/v1/feedback", { body: { message_id: message.id, rating, reason: why } }));
      await queryClient.invalidateQueries({ queryKey: threadKey(threadId) });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const button = "rounded px-1.5 py-0.5 hover:bg-slate-100 dark:hover:bg-slate-800 aria-pressed:bg-indigo-100 dark:aria-pressed:bg-indigo-900";
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs text-slate-500">
      <button type="button" className={button} aria-pressed={current === "up"} onClick={() => void rate("up", null)} aria-label="Helpful">
        👍
      </button>
      <button type="button" className={button} aria-pressed={current === "down"} onClick={() => setAsking(true)} aria-label="Not helpful">
        👎
      </button>
      {asking && (
        <form
          className="flex flex-1 gap-1"
          onSubmit={(event) => {
            event.preventDefault();
            setAsking(false);
            void rate("down", reason.trim() || null);
          }}
        >
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="What was wrong? (optional)"
            aria-label="What was wrong with this answer"
            maxLength={2000}
            className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-0.5 dark:border-slate-700 dark:bg-slate-950"
          />
          <button type="submit" className="rounded bg-slate-200 px-2 py-0.5 dark:bg-slate-700">
            Send
          </button>
        </form>
      )}
      {error && <span className="text-rose-600">{error}</span>}
    </div>
  );
}
