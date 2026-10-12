# Publishing

specfill ships through two independent channels: the Python package with the
TUI, and the agent skill. They share a version number.

## Python package

`.github/workflows/release.yml` runs the tests and publishes to PyPI through
trusted publishing whenever a `v*` tag is pushed. `uv build && uv publish`
does the same by hand.

## Agent skill

The repository itself is the distribution, so pushing to `master` publishes
the skill:

- `.claude-plugin/marketplace.json` makes the repository a Claude Code plugin
  marketplace named `specfill`, whose only plugin, also `specfill`, is the
  repository root (`"source": "./"`). Claude Code loads the skill from
  `skills/specfill/`.
- Codex reads the same `.claude-plugin` manifests, so
  `codex plugin marketplace add kklemon/specfill` works unchanged.
- `npx skills add kklemon/specfill` finds `skills/specfill/` and installs it
  for any of the agents the skills CLI supports. `agents/openai.yaml` inside
  the skill adds Codex's display metadata.

New installs always get the current `master`. Existing Claude Code and Codex
installs only update when `version` in `.claude-plugin/plugin.json` changes,
so bump it together with `pyproject.toml`; the tests enforce that both match.

Before pushing changes to the skill:

```sh
uv run pytest
claude plugin validate --strict .
```

The repository root is also the plugin root, so Claude Code would load any
root-level `agents/`, `commands/`, `hooks/`, `bin/`, `.mcp.json`, or
`settings.json` as part of the plugin. Keep those names out of the root.

### Directories and listings

- **Anthropic plugin directory**: submit through
  [claude.ai/directory/manage](https://claude.ai/directory/manage), following
  the [submission guide](https://claude.com/docs/plugins/submit) and the
  [pre-submission checklist](https://claude.com/docs/plugins/pre-submission-checklist).
  Submitting needs a paid claude.ai plan, and the portal checks more than
  `claude plugin validate --strict` does.
- **skills.sh**: there is nothing to submit. The leaderboard ranks public
  GitHub skills by the anonymous install telemetry of `npx skills`, so the
  skill shows up once people install it.
- **Codex**: there is no documented submission process for third-party
  plugins; users install from this repository's marketplace.
