"""CLI tests for the `specfill config` subcommands."""

from specfill.app import _config_set
from specfill.config import default_settings, load_settings


def test_config_set_provider_drops_the_previous_base_url():
    settings = default_settings("openai-compatible").model_copy(
        update={"model": "local-model", "base_url": "http://localhost:1234/v1"}
    )

    assert _config_set(settings, "provider", "anthropic") == 0

    saved = load_settings()
    assert saved is not None
    assert saved.provider == "anthropic"
    assert saved.base_url == ""


def test_config_set_rejects_an_unknown_provider():
    assert _config_set(default_settings("openai"), "provider", "not-a-provider") == 1
    assert load_settings() is None


def test_config_set_round_trips_a_value():
    assert _config_set(default_settings("openai"), "model", "gpt-6") == 0

    saved = load_settings()
    assert saved is not None
    assert saved.model == "gpt-6"
