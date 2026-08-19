"""Schemas for the interview loop and helpers to serialize Q&A for the LLM."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    label: str = Field(description="Short option label, at most 8 words")
    description: str = Field(
        default="", description="One-line implication of choosing this option"
    )


class Question(BaseModel):
    header: str = Field(description="1-3 word topic tag, e.g. 'Auth' or 'Data model'")
    text: str = Field(description="The full clarifying question")
    kind: Literal["single", "multi"] = Field(
        description="'single' for mutually exclusive choices, 'multi' when several options can apply"
    )
    options: list[QuestionOption] = Field(min_length=2, max_length=6)


class QuestionBatch(BaseModel):
    """One round of clarifying questions for the user."""

    questions: list[Question] = Field(min_length=1, max_length=6)


class InterviewComplete(BaseModel):
    """Signal that the seed prompt is now sufficiently specified."""

    summary: str = Field(
        default="", description="One sentence on what was clarified overall"
    )


@dataclass
class Answer:
    """A user's answer to one question. UI-side only, never LLM output."""

    question: Question
    selected: list[str] = field(default_factory=list)
    other_text: str = ""
    skipped: bool = False


SKIP_NOTE = "(skipped — leave unspecified, use your judgment; do not re-ask)"


def _format_answer(answer: Answer) -> str:
    if answer.skipped:
        return f"Answer: {SKIP_NOTE}"
    lines = []
    if answer.selected:
        lines.append(f"Answer: {'; '.join(answer.selected)}")
    if answer.other_text:
        prefix = "Other/free text" if answer.selected else "Answer (free text)"
        lines.append(f"{prefix}: {answer.other_text}")
    return "\n".join(lines)


def format_round_feedback(answers: list[Answer]) -> str:
    """Serialize one round of answers to feed back to the interviewer."""
    blocks = []
    for a in answers:
        blocks.append(f"**[{a.question.header}] {a.question.text}**\n{_format_answer(a)}")
    return "Here are my answers:\n\n" + "\n\n".join(blocks)


def format_transcript(answers: list[Answer]) -> str:
    """Serialize the full Q&A transcript for the rewriter."""
    blocks = []
    for a in answers:
        blocks.append(f"### [{a.question.header}] {a.question.text}\n{_format_answer(a)}")
    return "\n\n".join(blocks)
