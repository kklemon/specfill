"""Headless end-to-end drives of the TUI with scripted models — no network."""

import asyncio
import json

from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from textual.widgets import Select

import specfill.app as app_mod
from specfill.app import (
    InterviewScreen,
    PasteScreen,
    QuestionCard,
    ResultScreen,
    SpecfillApp,
    WizardScreen,
)
from specfill.config import (
    codex_auth_path,
    config_path,
    default_settings,
    get_api_key,
    load_settings,
)
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
    stream_kwargs = {}

    async def fake_stream(model, seed, answers, custom_instructions=""):
        collected.extend(answers)
        stream_kwargs["custom_instructions"] = custom_instructions
        for fragment in ["# Refined prompt\n\n", "With answers woven in."]:
            yield fragment
            await asyncio.sleep(0)

    monkeypatch.setattr(app_mod, "stream_rewrite", fake_stream)

    app = SpecfillApp(
        prefill="Build a thing that does stuff.",
        settings=TEST_SETTINGS,
        model=FunctionModel(model_fn),
    )
    async with app.run_test(size=(100, 40)) as pilot:
        assert isinstance(app.screen, PasteScreen)
        app.screen.query_one("#custom_instructions").value = "also correct spelling mistakes"
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
        await _wait_for(pilot, lambda: app.screen.query_one("#result").source == FINAL)

        # The interview screen underneath must not keep its spinner animating:
        # Textual still renders the screen below the current one, so a live
        # spinner there forces a full repaint of the result screen at 16 Hz.
        interview = next(s for s in app.screen_stack if isinstance(s, InterviewScreen))
        assert not interview.has_class("loading")
        assert interview.query_one("#loader").auto_refresh is None

        await pilot.press("p")  # quit & print
    assert app.return_value == FINAL

    assert [a.selected for a in collected] == [["OAuth"], ["macOS"]]
    assert collected[1].other_text == "maybe Windows later"
    assert not collected[0].skipped
    assert stream_kwargs["custom_instructions"] == "also correct spelling mistakes"


async def test_skip_and_finish_now(monkeypatch):
    def model_fn(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="ask_questions", args=BATCH.model_dump())]
        )

    async def fake_stream(model, seed, answers, custom_instructions=""):
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

    async def fake_stream(model, seed, answers, custom_instructions=""):
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
        app.screen.query_one("#reasoning_effort", Select).value = "high"
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
    assert saved.reasoning_effort == "high"
    assert get_api_key(saved) == "sk-wizard-test"


async def test_settings_screen_edits_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    def model_fn(messages, info):
        raise AssertionError("the model must not be called")

    app = SpecfillApp(settings=TEST_SETTINGS, model=FunctionModel(model_fn))
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, WizardScreen)

        select = app.screen.query_one("#reasoning_effort", Select)
        assert select.value == "default"
        select.value = "xhigh"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: isinstance(app.screen, PasteScreen))

        assert app.settings.reasoning_effort == "xhigh"
        # The model is rebuilt from the new settings, so the effort reaches it.
        assert app.model_instance.settings == {"openai_reasoning_effort": "xhigh"}

    saved = load_settings()
    assert saved is not None
    assert saved.reasoning_effort == "xhigh"


async def test_first_launch_wizard_accepts_codex_oauth_without_api_key():
    path = codex_auth_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"tokens": {"access_token": "oauth-test", "account_id": "account-test"}}
        )
    )

    app = SpecfillApp(settings=None, model=None)
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        wizard = app.screen
        assert isinstance(wizard, WizardScreen)

        wizard.query("#provider RadioButton")[1].value = True
        await pilot.pause()
        assert wizard.query_one("#api_key").disabled is True
        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: isinstance(app.screen, PasteScreen))
        assert app.settings is not None
        assert app.settings.provider == "openai-subscription"

    saved = load_settings()
    assert saved is not None
    assert saved.provider == "openai-subscription"


async def test_first_launch_wizard_accepts_specfill_api_key_from_environment(monkeypatch):
    """SPECFILL_* is documented as an override, but the wizard ignored this one.

    The wizard resolves a candidate built by `default_settings()`, which skips
    the environment by design, so it refused to save with a valid key present.
    """
    monkeypatch.setenv("SPECFILL_API_KEY", "sk-from-env")

    app = SpecfillApp(settings=None, model=None)
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        wizard = app.screen
        assert isinstance(wizard, WizardScreen)
        assert "stored" in wizard.query_one("#api_key").placeholder

        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: isinstance(app.screen, PasteScreen))
        assert app.settings is not None

    saved = load_settings()
    assert saved is not None
    assert "sk-from-env" not in config_path().read_text(), "an env key must not be persisted"


async def test_switching_provider_drops_the_previous_base_url(monkeypatch):
    """A base URL typed for one provider used to follow the user to the next."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    configured = default_settings("openai-compatible").model_copy(
        update={"model": "local-model", "base_url": "http://localhost:1234/v1"}
    )

    app = SpecfillApp(settings=configured, model=None)
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await _wait_for(pilot, lambda: isinstance(app.screen, WizardScreen))
        wizard = app.screen
        assert wizard.query_one("#base_url").value == "http://localhost:1234/v1"

        anthropic = app_mod.PROVIDER_ORDER.index("anthropic")
        wizard.query("#provider RadioButton")[anthropic].value = True
        await pilot.pause()
        assert wizard.query_one("#base_url").value == "", "stale base URL carried over"

        await pilot.press("ctrl+s")
        await _wait_for(pilot, lambda: isinstance(app.screen, PasteScreen))

    saved = load_settings()
    assert saved is not None
    assert saved.provider == "anthropic"
    assert saved.base_url == ""
