"""Offline tests for the agents layer — no network, scripted models only."""

import json

from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from specfill.agents import MAX_ROUNDS, InterviewSession, build_model
from specfill.config import codex_auth_path, default_settings
from specfill.models import (
    Answer,
    InterviewComplete,
    Question,
    QuestionBatch,
    QuestionOption,
    format_round_feedback,
    format_transcript,
)

models.ALLOW_MODEL_REQUESTS = False


def test_build_subscription_model_uses_codex_endpoint_and_account_header():
    path = codex_auth_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "oauth-test",
                    "account_id": "account-test",
                }
            }
        )
    )

    model = build_model(default_settings("openai-subscription"), "oauth-test")
    client = model.provider.client
    assert str(client.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert client.api_key == "oauth-test"
    assert client.default_headers["ChatGPT-Account-Id"] == "account-test"
    assert client.default_headers["originator"] == "specfill"
    assert model.settings == {"openai_store": False}


def make_question(header: str = "Auth", kind: str = "single") -> Question:
    return Question(
        header=header,
        text=f"Which {header.lower()} approach should be used?",
        kind=kind,
        options=[
            QuestionOption(label="Option A", description="the simple one"),
            QuestionOption(label="Option B"),
        ],
    )


async def test_scripted_interview_flow():
    calls = 0

    def model_fn(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            batch = QuestionBatch(questions=[make_question(), make_question("Deploy", "multi")])
            return ModelResponse(
                parts=[ToolCallPart(tool_name="ask_questions", args=batch.model_dump())]
            )
        return ModelResponse(
            parts=[ToolCallPart(tool_name="finish_interview", args={"summary": "done"})]
        )

    session = InterviewSession("Build an app.", FunctionModel(model_fn), web_search=False)
    outcome = await session.start()
    assert isinstance(outcome, QuestionBatch)
    assert len(outcome.questions) == 2

    answers = [
        Answer(question=outcome.questions[0], selected=["Option A"]),
        Answer(question=outcome.questions[1], skipped=True),
    ]
    outcome2 = await session.submit_answers(answers)
    assert isinstance(outcome2, InterviewComplete)
    assert outcome2.summary == "done"
    assert session.transcript == answers
    assert session.rounds_completed == 1
    assert calls == 2


async def test_round_cap_returns_complete_without_llm_call():
    def model_fn(messages, info):
        raise AssertionError("the model must not be called once the round cap is hit")

    session = InterviewSession("x", FunctionModel(model_fn), web_search=False)
    session.rounds_completed = MAX_ROUNDS - 1
    answers = [Answer(question=make_question(), skipped=True)]
    outcome = await session.submit_answers(answers)
    assert isinstance(outcome, InterviewComplete)
    assert session.rounds_completed == MAX_ROUNDS
    assert session.transcript == answers


async def test_transcript_not_recorded_on_llm_failure():
    def model_fn(messages, info):
        raise RuntimeError("boom")

    session = InterviewSession("x", FunctionModel(model_fn), web_search=False)
    answers = [Answer(question=make_question(), selected=["Option A"])]
    try:
        await session.submit_answers(answers)
    except Exception:
        pass
    assert session.transcript == []
    assert session.rounds_completed == 0


async def test_search_failure_warns_and_falls_back():
    """A failed run on the search-enabled agent retries without search and warns once."""
    calls = 0

    def model_fn(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:  # the search-enabled attempt fails at request time
            raise RuntimeError("search backend exploded")
        return ModelResponse(
            parts=[ToolCallPart(tool_name="finish_interview", args={"summary": "ok"})]
        )

    warnings: list[str] = []
    session = InterviewSession(
        "x",
        FunctionModel(model_fn),
        web_search=True,
        on_warning=warnings.append,
    )
    outcome = await session.start()
    assert isinstance(outcome, InterviewComplete)
    assert session.search_enabled is False
    assert warnings == ["Web search failed — continuing without it."]
    assert calls == 2


async def test_custom_instructions_reach_the_model():
    seen = {}

    def model_fn(messages, info):
        seen["instructions"] = messages[0].instructions
        return ModelResponse(
            parts=[ToolCallPart(tool_name="finish_interview", args={"summary": "ok"})]
        )

    session = InterviewSession(
        "x",
        FunctionModel(model_fn),
        web_search=False,
        custom_instructions="focus on the scraping part",
    )
    await session.start()
    assert "focus on the scraping part" in seen["instructions"]
    assert "expert requirements analyst" in seen["instructions"]  # base rules kept


def test_format_round_feedback_selected_and_skipped():
    q1, q2 = make_question(), make_question("Deploy")
    text = format_round_feedback(
        [
            Answer(question=q1, selected=["Option A"], other_text="plus a caveat"),
            Answer(question=q2, skipped=True),
        ]
    )
    assert "Answer: Option A" in text
    assert "Other/free text: plus a caveat" in text
    assert "skipped — leave unspecified" in text
    assert q1.text in text and q2.text in text


def test_format_transcript_free_text_only():
    q = make_question()
    text = format_transcript([Answer(question=q, other_text="use magic links")])
    assert "Answer (free text): use magic links" in text
    assert text.startswith(f"### [{q.header}]")
