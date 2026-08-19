"""Textual TUI: configure once, paste a seed prompt, get interviewed, receive the refined prompt."""

import sys
from pathlib import Path
from time import monotonic

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    Markdown,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
    TextArea,
)
from textual.widgets.selection_list import Selection

from .agents import InterviewSession, build_model, stream_rewrite
from .config import (
    PROVIDERS,
    Settings,
    config_path,
    default_settings,
    get_api_key,
    load_settings,
    save_settings,
    store_api_key,
)
from .models import Answer, Question, QuestionBatch

PROVIDER_ORDER = list(PROVIDERS)


class QuestionCard(Vertical):
    """Renders one question: options (radio or multi-select) plus an Other input."""

    def __init__(self, question: Question) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        q = self.question
        yield Static(Text(q.header), id="question_header")
        yield Static(Text(q.text), id="question_text")
        if q.kind == "single":
            with RadioSet(id="choices"):
                for option in q.options:
                    yield RadioButton(self._option_label(option))
                yield RadioButton(Text("Other (type below)", style="italic"))
        else:
            yield SelectionList[int](
                *[
                    Selection(self._option_label(option), i)
                    for i, option in enumerate(q.options)
                ],
                id="choices",
            )
        yield Input(placeholder="Other / details…", id="other")

    @staticmethod
    def _option_label(option) -> Text:
        label = Text(option.label)
        if option.description:
            label.append(f" — {option.description}", style="dim")
        return label

    @on(Input.Changed, "#other")
    def _auto_select_other(self, event: Input.Changed) -> None:
        if self.question.kind == "single" and event.value.strip():
            radios = list(self.query_one(RadioSet).query(RadioButton))
            if not any(r.value for r in radios[:-1]):
                radios[-1].value = True

    def collect(self) -> Answer | None:
        """Return the answer, or None if nothing was provided."""
        q = self.question
        other = self.query_one("#other", Input).value.strip()
        if q.kind == "single":
            idx = self.query_one(RadioSet).pressed_index
            if idx is not None and 0 <= idx < len(q.options):
                return Answer(question=q, selected=[q.options[idx].label], other_text=other)
            if other:  # "Other" radio (or nothing) pressed, with text provided
                return Answer(question=q, other_text=other)
            return None
        selected = [q.options[i].label for i in self.query_one(SelectionList).selected]
        if not selected and not other:
            return None
        return Answer(question=q, selected=selected, other_text=other)


class WizardScreen(Screen):
    """First-launch configuration wizard; also the in-app settings editor."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, first_launch: bool = False) -> None:
        super().__init__()
        self.first_launch = first_launch
        self._provider = "openai"

    def on_mount(self) -> None:
        self.query_one("#provider").focus()

    def compose(self) -> ComposeResult:
        current = self.app.settings if isinstance(self.app, SpecfillApp) else None
        self._provider = current.provider if current else "openai"
        yield Header()
        with VerticalScroll(id="wizard_form"):
            yield Static(
                "Welcome to specfill — configure your LLM provider to get started."
                if self.first_launch
                else "Settings — provider and model configuration.",
                id="wizard_title",
            )
            yield Label("Provider")
            with RadioSet(id="provider"):
                for name in PROVIDER_ORDER:
                    yield RadioButton(
                        PROVIDERS[name].label, value=(name == self._provider)
                    )
            yield Label("Model")
            yield Input(
                value=current.model if current else PROVIDERS[self._provider].default_model,
                placeholder="model identifier",
                id="model",
            )
            yield Label("API key")
            yield Input(password=True, placeholder=self._key_placeholder(), id="api_key")
            yield Label("Base URL")
            yield Input(
                value=current.base_url if current else "",
                placeholder=self._base_url_placeholder(),
                id="base_url",
            )
        with Horizontal(id="actions"):
            yield Button("Save", variant="primary", id="save")
            if not self.first_launch:
                yield Button("Cancel", id="cancel")
        yield Footer()

    def _candidate_settings(self) -> Settings:
        current = self.app.settings if isinstance(self.app, SpecfillApp) else None
        if current and current.provider == self._provider:
            return current
        return default_settings(self._provider)

    def _key_placeholder(self) -> str:
        if get_api_key(self._candidate_settings()):
            return "stored — leave blank to keep"
        env_var = PROVIDERS[self._provider].env_var
        return f"API key (or set ${env_var})"

    def _base_url_placeholder(self) -> str:
        if PROVIDERS[self._provider].needs_base_url:
            return "https://… (required for OpenAI-compatible servers)"
        return "custom base URL (optional)"

    @on(RadioSet.Changed, "#provider")
    def _on_provider_changed(self, event: RadioSet.Changed) -> None:
        previous = self._provider
        self._provider = PROVIDER_ORDER[event.radio_set.pressed_index]
        model_input = self.query_one("#model", Input)
        value = model_input.value.strip()
        if not value or value == PROVIDERS[previous].default_model:
            model_input.value = PROVIDERS[self._provider].default_model
        self.query_one("#api_key", Input).placeholder = self._key_placeholder()
        self.query_one("#base_url", Input).placeholder = self._base_url_placeholder()

    @on(Button.Pressed, "#save")
    def _on_save(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    def action_save(self) -> None:
        model = self.query_one("#model", Input).value.strip()
        key = self.query_one("#api_key", Input).value.strip()
        base_url = self.query_one("#base_url", Input).value.strip()
        preset = PROVIDERS[self._provider]

        if not model:
            self.notify("Enter a model identifier.", severity="warning")
            return
        if preset.needs_base_url and not base_url:
            self.notify("A base URL is required for OpenAI-compatible servers.", severity="warning")
            return

        settings = self._candidate_settings().model_copy(
            update={"provider": self._provider, "model": model, "base_url": base_url}
        )
        if key:
            settings = store_api_key(settings, key)
            if settings.api_key_storage == "config":
                self.notify(
                    "System keyring unavailable — key saved to the config file instead.",
                    severity="warning",
                )
        elif not get_api_key(settings):
            self.notify(
                f"Enter an API key (or set ${preset.env_var}).", severity="warning"
            )
            return
        save_settings(settings)

        app = self.app
        assert isinstance(app, SpecfillApp)
        app.apply_settings(settings)
        if self.first_launch:
            app.switch_screen(PasteScreen(app.prefill))
        else:
            app.pop_screen()
            self.notify("Settings saved.")

    def action_cancel(self) -> None:
        if self.first_launch:
            self.app.exit(None)
        else:
            self.app.pop_screen()


class PasteScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "analyze", "Analyze"),
        Binding("ctrl+o", "settings", "Settings"),
    ]

    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Paste your project seed prompt below, then press Ctrl+S (or the button) to analyze it.",
            id="instructions",
        )
        yield TextArea(self._prefill, id="seed")
        with Horizontal(id="actions"):
            yield Button("Analyze", variant="primary", id="analyze")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#seed").focus()

    @on(Button.Pressed, "#analyze")
    def _on_analyze(self) -> None:
        self.action_analyze()

    def action_analyze(self) -> None:
        text = self.query_one("#seed", TextArea).text.strip()
        if not text:
            self.notify("Paste a seed prompt first.", severity="warning")
            return
        self.app.push_screen(InterviewScreen(text))

    def action_settings(self) -> None:
        self.app.push_screen(WizardScreen())


class InterviewScreen(Screen):
    BINDINGS = [
        Binding("ctrl+n", "answer", "Answer"),
        Binding("ctrl+k", "skip", "Skip"),
        Binding("ctrl+f", "finish", "Finish now"),
        Binding("ctrl+r", "retry", "Retry", show=False),
    ]

    def __init__(self, seed: str) -> None:
        super().__init__()
        self.seed = seed
        self.session: InterviewSession | None = None
        self.pending: list[Question] = []
        self.index = 0
        self.round_answers: list[Answer] = []
        self._retry_op = "start"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="status")
        yield Static("", id="activity")
        yield LoadingIndicator(id="loader")
        yield Static("", id="error_box")
        yield Vertical(id="card_slot")
        with Horizontal(id="actions"):
            yield Button("Answer", variant="primary", id="answer")
            yield Button("Skip", id="skip")
            yield Button("Finish now", variant="warning", id="finish")
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        assert isinstance(app, SpecfillApp)
        web_search = app.settings.web_search if app.settings else True
        self.session = InterviewSession(
            self.seed,
            app.model_instance,
            web_search=web_search,
            # The activity handler opts into response streaming; only wire it
            # when there is research progress to show.
            on_activity=self._on_activity if web_search else None,
            on_warning=lambda msg: self.notify(msg, severity="warning"),
        )
        self.begin()

    def _on_activity(self, message: str) -> None:
        self.query_one("#activity", Static).update(Text(f"🔍 {message}"))

    # --- workers -----------------------------------------------------------

    @work(exclusive=True, group="llm")
    async def begin(self) -> None:
        self._retry_op = "start"
        self._set_loading("Researching and analyzing your prompt…")
        try:
            outcome = await self.session.start()
        except Exception as exc:
            self._show_error(exc)
            return
        await self._handle_outcome(outcome)

    @work(exclusive=True, group="llm")
    async def next_round(self) -> None:
        self._retry_op = "round"
        self._set_loading("Thinking about follow-ups…")
        try:
            outcome = await self.session.submit_answers(self.round_answers)
        except Exception as exc:
            self._show_error(exc)
            return
        await self._handle_outcome(outcome)

    # --- flow --------------------------------------------------------------

    async def _handle_outcome(self, outcome) -> None:
        if isinstance(outcome, QuestionBatch):
            questions = [q for q in outcome.questions if q.text.strip()]
            if questions:
                self.pending = questions
                self.index = 0
                self.round_answers = []
                self.remove_class("loading")
                self.query_one("#activity", Static).update("")
                await self._show_card()
                return
        self._to_result(self.session.transcript)

    async def _show_card(self) -> None:
        status = (
            f"Round {self.session.rounds_completed + 1}"
            f" · Question {self.index + 1}/{len(self.pending)}"
        )
        self.query_one("#status", Static).update(status)
        slot = self.query_one("#card_slot")
        await slot.remove_children()
        card = QuestionCard(self.pending[self.index])
        await slot.mount(card)
        card.query_one("#choices").focus()

    def _current_card(self) -> QuestionCard | None:
        try:
            return self.query_one(QuestionCard)
        except NoMatches:
            return None

    async def _advance(self) -> None:
        self.index += 1
        if self.index < len(self.pending):
            await self._show_card()
        else:
            self.next_round()

    def _to_result(self, answers: list[Answer]) -> None:
        self.workers.cancel_group(self, "llm")
        self.app.push_screen(ResultScreen(self.seed, answers))

    # --- ui state ----------------------------------------------------------

    def _set_loading(self, message: str) -> None:
        self.remove_class("errored")
        self.add_class("loading")
        self.query_one("#status", Static).update(message)
        self.query_one("#activity", Static).update("")

    def _show_error(self, exc: Exception) -> None:
        self.remove_class("loading")
        self.add_class("errored")
        self.query_one("#status", Static).update("Error")
        self.query_one("#error_box", Static).update(
            Text(
                f"LLM request failed: {exc}\n\n"
                "Press Ctrl+R to retry, or Ctrl+F to finish with the answers so far."
            )
        )
        self.notify("LLM request failed.", severity="error")

    # --- actions -----------------------------------------------------------

    @on(Button.Pressed, "#answer")
    async def _on_answer(self) -> None:
        await self.action_answer()

    @on(Button.Pressed, "#skip")
    async def _on_skip(self) -> None:
        await self.action_skip()

    @on(Button.Pressed, "#finish")
    def _on_finish(self) -> None:
        self.action_finish()

    async def action_answer(self) -> None:
        card = self._current_card()
        if card is None:
            return
        answer = card.collect()
        if answer is None:
            self.notify("Select an option, type in Other, or Skip.", severity="warning")
            return
        self.round_answers.append(answer)
        await self._advance()

    async def action_skip(self) -> None:
        if self._current_card() is None:
            return
        self.round_answers.append(Answer(question=self.pending[self.index], skipped=True))
        await self._advance()

    def action_finish(self) -> None:
        self._to_result(self.session.transcript + self.round_answers)

    def action_retry(self) -> None:
        if not self.has_class("errored"):
            return
        if self._retry_op == "start":
            self.begin()
        else:
            self.next_round()


class ResultScreen(Screen):
    BINDINGS = [
        Binding("c", "copy", "Copy"),
        Binding("r", "regenerate", "Regenerate"),
        Binding("p", "quit_print", "Quit & print"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self, seed: str, answers: list[Answer]) -> None:
        super().__init__()
        self.seed = seed
        self.answers = answers
        self.final_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="status")
        with VerticalScroll(id="result_scroll"):
            yield Markdown("", id="result")
        with Horizontal(id="actions"):
            yield Button("Copy", variant="primary", id="copy")
            yield Button("Regenerate", id="regenerate")
            yield Button("Quit & print", id="quit_print")
            yield Button("Quit", id="quit")
        yield Footer()

    def _has_evidence(self) -> bool:
        return any(not a.skipped for a in self.answers)

    def on_mount(self) -> None:
        if self._has_evidence():
            self.generate()
        else:
            self.final_text = self.seed
            self.query_one("#result", Markdown).update(self.seed)
            self.query_one("#status", Static).update(
                "No new information gathered — showing the original prompt."
            )

    @work(exclusive=True, group="rewrite")
    async def generate(self) -> None:
        app = self.app
        assert isinstance(app, SpecfillApp)
        status = self.query_one("#status", Static)
        md = self.query_one("#result", Markdown)
        status.update("Generating refined prompt…")
        last = 0.0
        try:
            async for text in stream_rewrite(app.model_instance, self.seed, self.answers):
                self.final_text = text
                now = monotonic()
                if now - last > 0.2:
                    md.update(text)
                    last = now
        except Exception as exc:
            md.update(self.final_text)
            status.update(Text(f"Rewrite failed: {exc} — press r to regenerate."))
            self.notify("Rewrite failed.", severity="error")
            return
        md.update(self.final_text)
        status.update(
            f"Done — {len(self.final_text):,} characters."
            "  c: copy · r: regenerate · p: quit & print to stdout"
        )

    @on(Button.Pressed, "#copy")
    def _on_copy(self) -> None:
        self.action_copy()

    @on(Button.Pressed, "#regenerate")
    def _on_regenerate(self) -> None:
        self.action_regenerate()

    @on(Button.Pressed, "#quit_print")
    def _on_quit_print(self) -> None:
        self.action_quit_print()

    @on(Button.Pressed, "#quit")
    def _on_quit(self) -> None:
        self.action_quit_app()

    def action_copy(self) -> None:
        if not self.final_text:
            self.notify("Nothing to copy yet.", severity="warning")
            return
        self.app.copy_to_clipboard(self.final_text)
        try:
            import pyperclip

            pyperclip.copy(self.final_text)
        except Exception:
            pass
        self.notify(
            f"Copied {len(self.final_text):,} characters to the clipboard."
        )

    def action_regenerate(self) -> None:
        if not self._has_evidence():
            self.notify("No interview answers — nothing to regenerate.", severity="warning")
            return
        self.notify("Regenerating from the same answers…")
        self.generate()

    def action_quit_print(self) -> None:
        self.app.exit(self.final_text or None)

    def action_quit_app(self) -> None:
        self.app.exit(None)


class SpecfillApp(App[str | None]):
    CSS_PATH = "app.tcss"
    TITLE = "specfill"

    def __init__(
        self,
        prefill: str = "",
        settings: Settings | None = None,
        model=None,
    ) -> None:
        super().__init__()
        self.prefill = prefill
        self.settings = settings
        self._model = model

    @property
    def model_instance(self):
        if self._model is None:
            assert self.settings is not None
            self._model = build_model(self.settings, get_api_key(self.settings))
        return self._model

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None  # rebuilt lazily from the new settings

    def on_mount(self) -> None:
        if self.settings is None:
            self.push_screen(WizardScreen(first_launch=True))
        else:
            self.push_screen(PasteScreen(self.prefill))


def _build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        prog="specfill",
        description="Interview yourself about the gaps in a project seed prompt.",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the TUI (the default command)")
    run.add_argument("file", nargs="?", help="prefill the prompt editor from this file")

    config = sub.add_parser("config", help="view or edit the configuration")
    config_sub = config.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="print the current configuration")
    config_sub.add_parser("path", help="print the config file location")
    set_p = config_sub.add_parser("set", help="set a configuration value")
    set_p.add_argument("key", choices=["provider", "model", "base-url", "web-search"])
    set_p.add_argument("value")
    config_sub.add_parser("set-key", help="store the API key (prompted, hidden input)")
    return parser


def _config_show(settings: Settings) -> int:
    stored = "yes" if get_api_key(settings) else "no"
    print(f"config file:  {config_path()}")
    print(f"provider:     {settings.provider}")
    print(f"model:        {settings.model}")
    print(f"base-url:     {settings.base_url or '(none)'}")
    print(f"web-search:   {str(settings.web_search).lower()}")
    print(f"api key:      {stored} (storage: {settings.api_key_storage})")
    return 0


def _config_set(settings: Settings, key: str, value: str) -> int:
    if key == "provider":
        if value not in PROVIDERS:
            print(
                f"specfill: unknown provider {value!r} (choose from: {', '.join(PROVIDERS)})",
                file=sys.stderr,
            )
            return 1
        update = {"provider": value}
        if not settings.model or settings.model == settings.preset.default_model:
            update["model"] = PROVIDERS[value].default_model
        settings = settings.model_copy(update=update)
    elif key == "model":
        settings = settings.model_copy(update={"model": value})
    elif key == "base-url":
        settings = settings.model_copy(update={"base_url": value})
    elif key == "web-search":
        if value.lower() not in ("true", "false"):
            print("specfill: web-search must be true or false", file=sys.stderr)
            return 1
        settings = settings.model_copy(update={"web_search": value.lower() == "true"})
    save_settings(settings)
    print(f"{key} = {value}")
    return 0


def _config_set_key(settings: Settings) -> int:
    import getpass

    key = getpass.getpass(f"API key for {settings.preset.label}: ").strip()
    if not key:
        print("specfill: no key entered", file=sys.stderr)
        return 1
    settings = store_api_key(settings, key)
    save_settings(settings)
    where = (
        "system keyring"
        if settings.api_key_storage == "keyring"
        else "config file (keyring unavailable)"
    )
    print(f"API key stored in {where}.")
    return 0


def _config_cli(args) -> int:
    settings = load_settings() or default_settings()
    command = args.config_command or "show"
    if command == "show":
        return _config_show(settings)
    if command == "path":
        print(config_path())
        return 0
    if command == "set":
        return _config_set(settings, args.key, args.value)
    if command == "set-key":
        return _config_set_key(settings)
    return 1


def main() -> None:
    argv = sys.argv[1:]
    # "specfill FILE" and bare "specfill" are shorthand for the "run" subcommand.
    if not argv or argv[0] not in ("run", "config", "-h", "--help"):
        argv = ["run", *argv]
    args = _build_parser().parse_args(argv)

    if args.command == "config":
        raise SystemExit(_config_cli(args))

    prefill = ""
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"specfill: file not found: {path}", file=sys.stderr)
            raise SystemExit(1)
        prefill = path.read_text()

    try:
        result = SpecfillApp(prefill, settings=load_settings()).run()
    except KeyboardInterrupt:
        raise SystemExit(130)
    if result:
        print(result)


if __name__ == "__main__":
    main()
