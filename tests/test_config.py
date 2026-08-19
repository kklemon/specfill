"""Config layer tests: save/load round-trip, keyring fallback, key resolution."""

import keyring
import keyring.errors

from specfill.config import (
    KEYRING_SERVICE,
    Settings,
    config_path,
    default_settings,
    get_api_key,
    load_settings,
    save_settings,
    store_api_key,
)


def test_first_launch_returns_none():
    assert load_settings() is None


def test_save_load_round_trip():
    settings = default_settings("anthropic").model_copy(
        update={"model": "claude-opus-5", "web_search": False}
    )
    path = save_settings(settings)
    assert path == config_path()
    assert (path.stat().st_mode & 0o777) == 0o600

    loaded = load_settings()
    assert loaded is not None
    assert loaded.provider == "anthropic"
    assert loaded.model == "claude-opus-5"
    assert loaded.web_search is False
    assert loaded.api_key_storage == "keyring"


def test_corrupt_config_returns_none():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("provider = 'not-a-provider'\n")
    assert load_settings() is None


def test_env_overrides_config_file(monkeypatch):
    save_settings(default_settings("openai"))
    monkeypatch.setenv("SPECFILL_MODEL", "gpt-6")
    loaded = load_settings()
    assert loaded.model == "gpt-6"
    assert loaded.provider == "openai"


def test_store_api_key_prefers_keyring(isolated_config):
    settings = store_api_key(default_settings("openai"), "sk-test")
    assert settings.api_key_storage == "keyring"
    assert settings.api_key == ""
    assert isolated_config[(KEYRING_SERVICE, "openai")] == "sk-test"
    assert get_api_key(settings) == "sk-test"


def test_store_api_key_falls_back_to_config(monkeypatch):
    def broken(*args):
        raise keyring.errors.KeyringError("no backend")

    monkeypatch.setattr(keyring, "set_password", broken)
    settings = store_api_key(default_settings("openai"), "sk-fallback")
    assert settings.api_key_storage == "config"
    assert settings.api_key == "sk-fallback"

    save_settings(settings)
    assert "sk-fallback" in config_path().read_text()
    assert get_api_key(load_settings()) == "sk-fallback"


def test_get_api_key_env_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    settings = default_settings("anthropic")
    assert get_api_key(settings) == "sk-env"


def test_keys_are_per_provider(isolated_config):
    store_api_key(default_settings("openai"), "sk-openai")
    store_api_key(default_settings("google"), "sk-google")
    assert get_api_key(default_settings("openai")) == "sk-openai"
    assert get_api_key(default_settings("google")) == "sk-google"
    assert get_api_key(default_settings("anthropic")) == ""


def test_keyring_key_not_written_to_config_file(isolated_config):
    settings = store_api_key(default_settings("openai"), "sk-secret")
    save_settings(settings)
    assert "sk-secret" not in config_path().read_text()


def test_settings_is_pydantic_settings():
    assert "SPECFILL_" == Settings.model_config.get("env_prefix")
