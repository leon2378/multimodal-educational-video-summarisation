"""Answering questions about a lecture or a course (docs/blueprint.md, section 5): make a
follow-up question stand on its own for search, then stream an answer from the retrieved
passages.

Passages and the conversation are untrusted, like everything else from a lecture: they go into
delimited, HTML-escaped blocks, and the prompts say they are content, never instructions.
"""

import html
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models import Model

from lecture_core.notes import format_timestamp
from lecture_core.qa import ChatTurn, Passage
from lecture_llm.agents import Prompt, Usage, attr, render_slide

# Earlier answers are shortened in prompts: they're context for the question, not evidence.
_ANSWER_CHARS = 600


@dataclass(frozen=True)
class QAPrompts:
    answer: Prompt
    course_answer: Prompt
    rewrite: Prompt

    @classmethod
    def load(cls, prompts_dir: Path) -> "QAPrompts":
        return cls(
            answer=Prompt.load(prompts_dir, "answer.v1"),
            course_answer=Prompt.load(prompts_dir, "course-answer.v1"),
            rewrite=Prompt.load(prompts_dir, "rewrite.v1"),
        )


class AnswerLLM:
    def __init__(self, model: Model, prompts: QAPrompts) -> None:
        self.model_name = f"{model.system}:{model.model_name}"
        self.prompts = prompts
        self._answer = Agent(model, instructions=prompts.answer.text)
        self._course_answer = Agent(model, instructions=prompts.course_answer.text)
        self._rewrite = Agent(model, instructions=prompts.rewrite.text)

    async def rewrite(self, question: str, history: Sequence[ChatTurn]) -> tuple[str, Usage]:
        """The question made standalone. Without earlier turns it already is: no model call."""
        usage = Usage()
        if not history:
            return question, usage
        result = await self._rewrite.run(
            f"{render_conversation(history)}\n\n<question>{html.escape(question)}</question>"
        )
        usage.add(result.usage)
        return result.output.strip() or question, usage

    async def stream_answer(
        self,
        question: str,
        passages: Sequence[Passage],
        history: Sequence[ChatTurn],
        usage: Usage,
    ) -> AsyncIterator[str]:
        """The answer as it's generated. `usage` is filled in once the stream ends. Labelled
        passages (lecture_core.qa.label_lectures) make it an answer across a course."""
        parts = [render_conversation(history)] if history else []
        parts += [render_passages(passages), f"<question>{html.escape(question)}</question>"]
        agent = self._course_answer if any(p.label for p in passages) else self._answer
        async with agent.run_stream("\n\n".join(parts)) as result:
            async for delta in result.stream_text(delta=True, debounce_by=None):
                yield delta
            usage.add(result.usage)


def render_passages(passages: Sequence[Passage]) -> str:
    blocks = []
    for passage in passages:
        span = f"{format_timestamp(passage.start_s)}-{format_timestamp(passage.end_s)}"
        attrs = f'time="{span}"' + (f" chapter={attr(passage.chapter)}" if passage.chapter else "")
        if passage.label:
            lecture = f"{passage.label}: {passage.lecture_title or 'Untitled'}"
            attrs = f"lecture={attr(lecture)} {attrs}"
        prefix = f"{passage.label} " if passage.label else ""
        lines = [f"<passage {attrs}>"]
        if passage.slide is not None and (slide := render_slide(passage.slide)):
            lines.append(f"<slide>\n{slide}\n</slide>")
        lines.append("<speech>")
        lines += [
            f"[{prefix}{format_timestamp(s.start_s)}] {html.escape(s.text)}"
            for s in passage.sentences
        ]
        lines += ["</speech>", "</passage>"]
        blocks.append("\n".join(lines))
    return "<passages>\n" + "\n".join(blocks) + "\n</passages>"


def render_conversation(history: Sequence[ChatTurn]) -> str:
    turns = []
    for turn in history:
        answer = turn.answer
        if len(answer) > _ANSWER_CHARS:
            answer = answer[:_ANSWER_CHARS] + "…"
        turns.append(
            f"<turn>\n<question>{html.escape(turn.question)}</question>\n"
            f"<answer>{html.escape(answer)}</answer>\n</turn>"
        )
    return "<conversation>\n" + "\n".join(turns) + "\n</conversation>"
