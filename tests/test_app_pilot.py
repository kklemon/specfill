"""Headless end-to-end drives of the TUI with scripted models — no network."""

import asyncio

from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import specfill.app as app_mod
from specfill.app import (
    InterviewScreen,
    PasteScreen,
    QuestionCard,
    ResultScreen,
    SpecfillApp,
    WizardScreen,
)
from specfill.config import default_settings, get_api_key, load_settings
from specfill.models import Question, QuestionBatch, QuestionOption

models.ALLOW_MODEL_REQUESTS = False

BATCH = QuestionBatch(
    questions=[
        Question(
            header="Auth",
            text="Which auth method?",
            kind="single",
            options=[
                QuestionOption(label="OAuth", description="via GitHub"),
                QuestionOption(label="JWT"),
            ],
        ),
        Question(
            header="Platforms",
            text="Which platforms to support?",
            kind="multi",
            options=[QuestionOption(label="macOS"), QuestionOption(label="Linux")],
        ),
    ]
)

FINAL = "# Refined prompt\n\nWith answers woven in."

TEST_SETTINGS = default_settings("openai").model_copy(update={"web_search": False})


async def _wait_for(pilot, predicate, tries: int = 100):
    for _ in range(tries):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError("condition not reached")


async def test_full_flow(monkeypatch):
    calls = 0

    def model_fn(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="ask_questions", args=BATCH.model_dump())]
            )
        return ModelResponse(
            parts=[ToolCallPart(tool_name="finish_interview", args={"summary": "ok"})]
        )

    collected = []

    async def fake_stream(model, seed, answers):
        collected.extend(answers)
        for chunk in ["# Refined", FINAL]:
            yield chunk
            await asyncio.sleep(0)

    monkeypatch.setattr(app_mod, "stream_rewrite", fake_stream)

    app = SpecfillApp(
        prefill="Build a thing that does stuff.",
        settings=TEST_SETTINGS,
        model=FunctionModel(model_fn),
    )
    async with app.run_test(size=(100, 40)) as pilot:
        assert isinstance(app.screen, PasteScreen)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, InterviewScreen)

        await _wait_for(pilot, lambda: bool(app.screen.query(QuestionCard)))
        assert app.screen.query_one(QuestionCard).question.header == "Auth"

        # Q1 (single): press the highlighted first radio, answer
        await pilot.press("enter")
        await pilot.press("ctrl+n")
        await pilot.pause()
        card = app.screen.query_one(QuestionCard)
        assert card.question.header == "Platforms"

        # Q2 (multi): toggle first selection + type Other text
        sel = card.query_one("SelectionList")
        sel.select(sel.get_option_at_index(0))
        card.query_one("#other").value = "maybe Windows later"
        await pilot.press("ctrl+n")

        await _wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await _wait_for(pilot, lambda: app.screen.final_text == FINAL)

        await pilot.press("p")  # quit & print
    assert app.return_value == FINAL

    assert [a.selected for a in collected] == [["OAuth"], ["macOS"]]
    assert collected[1].other_text == "maybe Windows later"
    assert not collected[0].skipped


async def test_skip_and_finish_now(monkeypatch):
    def model_fn(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="ask_questions", args=BATCH.model_dump())]
        )

    async def fake_stream(model, seed, answers):
        yield FINAL

    monkeypatch.setattr(app_mod, "stream_rewrite", fake_stream)

    app = SpecfillApp(
        prefill="Another seed.", settings=TEST_SETTINGS, model=FunctionModel(model_fn)
    )
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: bool(app.screen.query(QuestionCard)))

        await pilot.press("ctrl+k")  # skip Q1
        await pilot.pause()
        assert app.screen.query_one(QuestionCard).question.header == "Platforms"

        await pilot.press("ctrl+f")  # finish now mid-round
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        # one skipped answer only -> no informative answers -> original shown
        await _wait_for(pilot, lambda: app.screen.final_text == "Another seed.")
        await pilot.press("q")
    assert app.return_value is None


async def test_regenerate_uses_same_evidence(monkeypatch):
    def model_fn(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="finish_interview", args={"summary": "ok"})]
        )

    stream_calls = []

    async def fake_stream(model, seed, answers):
        stream_calls.append(list(answers))
        yield FINAL

    monkeypatch.setattr(app_mod, "stream_rewrite", fake_stream)

    from specfill.models import Answer

    answers = [Answer(question=BATCH.questions[0], selected=["OAuth"])]
    app = SpecfillApp(settings=TEST_SETTINGS, model=FunctionModel(model_fn))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(ResultScreen("Seed.", answers))
        await _wait_for(pilot, lambda: app.screen.final_text == FINAL)
        await pilot.press("r")  # regenerate with the same evidence
        await _wait_for(pilot, lambda: len(stream_calls) == 2)
        assert stream_calls[0] == stream_calls[1] == answers
        await pilot.press("q")


async def test_first_launch_wizard_saves_and_continues():
    app = SpecfillApp(settings=None, model=None)
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WizardScreen)

        app.screen.query_one("#model").value = "gpt-5.6-sol"
        app.screen.query_one("#api_key").value = "sk-wizard-test"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: isinstance(app.screen, PasteScreen))

        assert app.settings is not None
        assert app.settings.provider == "openai"
        assert app.settings.api_key_storage == "keyring"

    saved = load_settings()
    assert saved is not None
    assert saved.model == "gpt-5.6-sol"
    assert get_api_key(saved) == "sk-wizard-test"
