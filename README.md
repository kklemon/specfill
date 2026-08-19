# specfill

An interactive TUI that fills the gaps in your project seed prompts.

You paste a long "seed" prompt you intend to hand to a coding agent (or load it
from a file). An LLM agent researches the topic with model-native web search,
finds the aspects that are underspecified enough that the agent would have to
guess, and interviews you about them — plan-mode style, one question at a time,
with concrete arrow-key options, multi-select where it makes sense, an
"Other: enter text" escape hatch, and skipping. Questions come in adaptive
rounds (in the language of your prompt) until the spec is complete, or until
you press *Finish now*. The result is your original prompt, style and structure
faithfully preserved, with only the newly acquired information woven in — and
where an answer contradicts the original, the latest answer wins. Decisions you
left unresolved stay exactly as ambiguous as you wrote them; nothing is
invented.

## Installation

Requires Python ≥ 3.12 on macOS or Linux. Install as a
[uv tool](https://docs.astral.sh/uv/guides/tools/):

```sh
uv tool install specfill        # from a checkout: uv tool install .
```

On first launch a configuration wizard collects your provider preset
(**OpenAI**, **OpenAI-compatible**, **Anthropic**, or **Google**), model
identifier, API key, and an optional custom base URL. The API key is stored in
the system keyring, with a fallback to the config file (chmod 600) when no
keyring backend is available.

## Usage

```sh
specfill                  # paste your prompt into the editor
specfill my-prompt.md     # or prefill it from a file
```

Flow:

1. **Paste** your seed prompt, press `Ctrl+S` to analyze. The agent researches
   with web search before questioning (progress shown live); if your model has
   no native search, or search fails mid-session, it warns and continues.
2. **Answer** the questions — arrow keys + `Enter`/`Space` to select,
   type into *Other / details…* for free-text answers.
   `Ctrl+N` answer · `Ctrl+K` skip · `Ctrl+F` finish now.
3. **Result** streams into a scrollable preview. `c` copies it to the clipboard
   (with confirmation), `r` regenerates it from the same answers, and `p` quits
   and prints the revised prompt — and only that — to stdout.

Skipped questions are treated as "implementer's discretion" and never re-asked.

## Configuration

Settings live in `~/.config/specfill/config.toml` (honors `$XDG_CONFIG_HOME`)
and can be edited three ways: the in-app settings screen (`Ctrl+O` on the paste
screen), the CLI, or the file itself.

```sh
specfill config show                    # current configuration
specfill config path                    # config file location
specfill config set provider anthropic  # provider | model | base-url | web-search
specfill config set model claude-opus-5
specfill config set-key                 # store the API key (hidden prompt)
```

Every setting can also be overridden per-invocation via `SPECFILL_*`
environment variables (e.g. `SPECFILL_MODEL`, `SPECFILL_WEB_SEARCH=false`).
API keys resolve from the keyring first, then the config file /
`$SPECFILL_API_KEY`, then the provider's conventional variable
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`).

LLM inference is provider-agnostic via [Pydantic AI](https://ai.pydantic.dev/)
(default model: OpenAI GPT-5.6 Sol); the UI is
[Textual](https://textual.textualize.io/).

## Development

```sh
uv sync
uv run pytest    # offline tests (scripted models, no network)
```
