"""Interviewer and rewriter agents plus the session orchestrating the interview.

Agents are built at runtime from user settings (provider, model, key). The
interviewer gets the model's native web search when enabled; on models without
native search the tool is dropped (optional=True), and on runtime search
failures the session warns and continues without it.
"""

import asyncio
from collections.abc import AsyncIterator, Callable

from pydantic_ai import Agent, ModelRetry, ToolOutput, WebSearchTool
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import NativeToolCallPart, PartEndEvent, PartStartEvent
from pydantic_ai.models import Model

from .config import Settings
from .models import (
    Answer,
    InterviewComplete,
    QuestionBatch,
    format_round_feedback,
    format_transcript,
)

# Purely runaway protection; the interviewer is instructed to be exhaustive and
# the user can always end the interview early from the UI.
MAX_ROUNDS = 12

INTERVIEWER_INSTRUCTIONS = """\
You are an expert requirements analyst. The user gives you a "seed prompt" — a
project brief they will hand to an AI coding agent. Your job is to find the
aspects that are underspecified enough that the coding agent would have to
guess, and to ask the user targeted clarifying questions.

Rules:
- Only ask questions whose answers materially change what gets built. Never ask
  about anything the prompt already answers or explicitly defers.
- Prefer concrete, opinionated options over open-ended asks: 2-6 options, each
  with a short label and a one-line description of its implications.
- Use kind="single" for mutually exclusive choices and kind="multi" when
  several options can apply simultaneously.
- Ask 3-6 questions per round, most important first.
- Write everything the user sees — questions, option labels, descriptions, and
  summaries — in the same language as the seed prompt.
- If a web search tool is available, research before questioning: look up
  unfamiliar tools, libraries, services, or claims named in the prompt so your
  questions and options are accurate and current, and search again during the
  interview whenever an answer introduces something you are unsure about.
  Never ask the user something you can resolve yourself with a quick search.
- The UI always offers the user a free-text "Other" escape hatch and a Skip.
  Treat a skipped question as "leave unspecified / implementer's discretion"
  and never re-ask it.
- Be exhaustive: keep asking follow-up rounds as long as material ambiguity
  remains, including ambiguity newly created by the user's answers. Only call
  finish_interview once the prompt is fully specified from an implementer's
  perspective — when a competent coding agent could execute it without having
  to guess on anything consequential.
"""

REWRITER_INSTRUCTIONS = """\
You revise a project "seed prompt" to incorporate answers from a clarification
interview. You receive the ORIGINAL PROMPT and a Q&A TRANSCRIPT.

Absolute rules:
- Preserve the original verbatim wherever possible: same headings, same section
  order, same list structure, same tone, same phrasing, same formatting quirks,
  even the same typos. You are a careful line editor, not an author.
- Write in the same language as the original prompt.
- Every change must encode information from the transcript, nothing else. Weave
  each answer into the most natural existing spot: extend a sentence, add a
  clause, add a bullet to an existing list. Add a new subsection only when no
  existing location fits, matching the surrounding heading style.
- When an answer conflicts with a statement in the original prompt, the latest
  answer wins: minimally edit that statement so it reflects the answer.
- If a question was skipped or the interview ended early, leave the aspect it
  covered exactly as ambiguous as the original left it — never invent an
  answer or resolve an open decision yourself.
- Do not summarize, restructure, reorder, "improve", or fix grammar/style.
- Never mention the interview, the questions, or that this is a revision.
- Output ONLY the revised prompt as markdown — no preamble, no closing remarks,
  no code fence around the document.
"""


def build_model(settings: Settings, api_key: str) -> Model:
    """Construct the Pydantic AI model instance for the configured provider."""
    base_url = settings.base_url.strip() or None
    if settings.provider == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIResponsesModel(
            settings.model, provider=OpenAIProvider(api_key=api_key, base_url=base_url)
        )
    if settings.provider == "openai-compatible":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(
            settings.model,
            provider=OpenAIProvider(api_key=api_key or "unused", base_url=base_url),
        )
    if settings.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return AnthropicModel(settings.model, provider=AnthropicProvider(**kwargs))
    if settings.provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return GoogleModel(settings.model, provider=GoogleProvider(**kwargs))
    raise ValueError(f"Unknown provider: {settings.provider}")


def _check_questions(output: QuestionBatch | InterviewComplete):
    if isinstance(output, QuestionBatch):
        for q in output.questions:
            if not q.text.strip():
                raise ModelRetry("A question has empty text; provide real questions.")
            labels = [o.label.strip() for o in q.options]
            if len(set(labels)) != len(labels):
                raise ModelRetry(
                    f"Question '{q.text[:50]}' has duplicate option labels; make them distinct."
                )
    return output


def make_interviewer(model: Model, *, web_search: bool) -> Agent:
    capabilities = None
    if web_search:
        # optional=True: silently dropped on models without native web search.
        capabilities = [WebSearch(native=WebSearchTool(optional=True))]
    agent = Agent(
        model,
        output_type=[
            ToolOutput(
                QuestionBatch,
                name="ask_questions",
                description="Ask the user another round of clarifying questions",
            ),
            ToolOutput(
                InterviewComplete,
                name="finish_interview",
                description="Declare the interview complete; no further questions needed",
            ),
        ],
        instructions=INTERVIEWER_INSTRUCTIONS,
        retries=2,
        capabilities=capabilities,
    )
    agent.output_validator(_check_questions)
    return agent


def make_rewriter(model: Model) -> Agent:
    return Agent(model, output_type=str, instructions=REWRITER_INSTRUCTIONS)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, ModelHTTPError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, (ModelAPIError, ConnectionError, TimeoutError))


async def _run_with_retries(coro_factory, attempts: int = 3):
    delay = 2.0
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt == attempts - 1 or not _is_transient(exc):
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


class InterviewSession:
    """Drives the iterative interview, carrying message history across rounds.

    on_activity receives short research-progress strings (e.g. web searches) to
    surface in the UI; on_warning receives one-off warnings (e.g. search failed).
    """

    def __init__(
        self,
        seed_prompt: str,
        model: Model,
        *,
        web_search: bool = True,
        on_activity: Callable[[str], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
    ):
        self.seed_prompt = seed_prompt
        self.transcript: list[Answer] = []
        self.rounds_completed = 0
        self.search_enabled = web_search
        self.on_activity = on_activity
        self.on_warning = on_warning
        self._messages = None
        self._search_agent = make_interviewer(model, web_search=True) if web_search else None
        self._plain_agent = make_interviewer(model, web_search=False)

    async def _handle_events(self, ctx, stream) -> None:
        async for event in stream:
            if not isinstance(event, (PartStartEvent, PartEndEvent)):
                continue
            part = event.part
            if not isinstance(part, NativeToolCallPart):
                continue
            query = self._search_query(part)
            if isinstance(event, PartStartEvent):
                message = f"Searching the web: {query}" if query else "Searching the web…"
            elif query:  # args often only complete by the end of the part
                message = f"Searched the web: {query}"
            else:
                continue
            if self.on_activity:
                self.on_activity(message)

    @staticmethod
    def _search_query(part: NativeToolCallPart) -> str | None:
        args = part.args
        if isinstance(args, str):
            import json

            try:
                args = json.loads(args)
            except ValueError:
                return None
        if isinstance(args, dict):
            value = args.get("query") or args.get("q")
            return str(value) if value else None
        return None

    async def _run(self, prompt: str) -> QuestionBatch | InterviewComplete:
        handler = self._handle_events if self.on_activity else None
        agent = self._search_agent if self.search_enabled and self._search_agent else self._plain_agent
        try:
            result = await _run_with_retries(
                lambda: agent.run(
                    prompt, message_history=self._messages, event_stream_handler=handler
                )
            )
        except Exception:
            if agent is not self._plain_agent:
                # Native search is nominally supported but failed: warn, then
                # continue the session without it.
                self.search_enabled = False
                if self.on_warning:
                    self.on_warning("Web search failed — continuing without it.")
                result = await _run_with_retries(
                    lambda: self._plain_agent.run(
                        prompt, message_history=self._messages, event_stream_handler=handler
                    )
                )
            else:
                raise
        self._messages = result.all_messages()
        return result.output

    async def start(self) -> QuestionBatch | InterviewComplete:
        return await self._run(
            "Analyze this seed prompt and begin the interview:\n\n"
            f"<seed_prompt>\n{self.seed_prompt}\n</seed_prompt>"
        )

    async def submit_answers(
        self, round_answers: list[Answer]
    ) -> QuestionBatch | InterviewComplete:
        if self.rounds_completed + 1 >= MAX_ROUNDS:
            self.transcript.extend(round_answers)
            self.rounds_completed += 1
            return InterviewComplete(summary="Round cap reached.")
        # Only record the round once the LLM call succeeds, so a UI retry after
        # a failure can safely call this again with the same answers.
        outcome = await self._run(format_round_feedback(round_answers))
        self.transcript.extend(round_answers)
        self.rounds_completed += 1
        return outcome


def build_rewriter_prompt(seed_prompt: str, answers: list[Answer]) -> str:
    return (
        f"<original_prompt>\n{seed_prompt}\n</original_prompt>\n\n"
        f"<qa_transcript>\n{format_transcript(answers)}\n</qa_transcript>\n\n"
        "Produce the revised prompt now."
    )


async def stream_rewrite(
    model: Model, seed_prompt: str, answers: list[Answer]
) -> AsyncIterator[str]:
    """Yield the accumulated rewritten prompt as it streams in."""
    rewriter = make_rewriter(model)
    async with rewriter.run_stream(build_rewriter_prompt(seed_prompt, answers)) as result:
        async for text in result.stream_text():
            yield text
