"""The agent skill and its plugin manifests stay valid and in sync with the TUI."""

import json
import re
import tomllib
from pathlib import Path

import yaml

from specfill.agents import MAX_ROUNDS, REWRITER_INSTRUCTIONS

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "specfill"


def _read_skill() -> tuple[dict, str]:
    _, frontmatter, body = (SKILL_DIR / "SKILL.md").read_text().split("---\n", 2)
    return yaml.safe_load(frontmatter), body


def _normalize(text: str) -> str:
    return " ".join(text.split())


def test_skill_frontmatter_follows_the_agent_skills_spec():
    meta, _ = _read_skill()
    # The portable subset: claude.ai uploads and Codex's validator reject other keys.
    assert set(meta) <= {"name", "description", "license", "allowed-tools", "metadata"}
    assert meta["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"])
    assert 0 < len(meta["description"]) <= 1024
    assert "<" not in meta["description"] and ">" not in meta["description"]


def test_skill_carries_the_rewriter_rules_verbatim():
    _, body = _read_skill()
    rules = REWRITER_INSTRUCTIONS.split("Absolute rules:\n", 1)[1].strip()
    for rule in re.split(r"\n(?=- )", rules):
        assert _normalize(rule) in _normalize(body), f"SKILL.md lacks the rewriter rule {rule!r}"


def test_skill_round_cap_matches_the_tui():
    _, body = _read_skill()
    assert f"after {MAX_ROUNDS} rounds" in body


def test_plugin_manifests_point_at_the_skill():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    # Claude Code only offers plugin updates when the version changes.
    assert plugin["version"] == pyproject["project"]["version"]
    [entry] = marketplace["plugins"]
    assert entry == entry | {"name": plugin["name"], "source": "./"}
    assert (ROOT / "skills" / plugin["name"] / "SKILL.md").is_file()
