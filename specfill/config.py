"""User settings: config file in the specfill config dir, API keys in the system keyring.

Settings precedence: constructor args > SPECFILL_* environment variables > config file.
Key resolution order: system keyring -> config file / $SPECFILL_API_KEY -> provider env var.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

KEYRING_SERVICE = "specfill"

ProviderName = Literal["openai", "openai-compatible", "anthropic", "google"]


@dataclass(frozen=True)
class ProviderPreset:
    label: str
    default_model: str
    env_var: str
    needs_base_url: bool = False


PROVIDERS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        label="OpenAI",
        default_model="gpt-5.6-sol",
        env_var="OPENAI_API_KEY",
    ),
    "openai-compatible": ProviderPreset(
        label="OpenAI-compatible",
        default_model="",
        env_var="OPENAI_API_KEY",
        needs_base_url=True,
    ),
    "anthropic": ProviderPreset(
        label="Anthropic",
        default_model="claude-opus-5",
        env_var="ANTHROPIC_API_KEY",
    ),
    "google": ProviderPreset(
        label="Google",
        default_model="gemini-3.1-pro-preview",
        env_var="GOOGLE_API_KEY",
    ),
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "specfill"


def config_path() -> Path:
    return config_dir() / "config.toml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPECFILL_", extra="ignore")

    provider: ProviderName = "openai"
    model: str = "gpt-5.6-sol"
    base_url: str = ""
    web_search: bool = True
    api_key_storage: Literal["keyring", "config"] = "keyring"
    api_key: str = ""  # only populated when api_key_storage == "config"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # config_path() is resolved at construction time so XDG_CONFIG_HOME
        # changes (and tests) are honored.
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_path()),
        )

    @property
    def preset(self) -> ProviderPreset:
        return PROVIDERS[self.provider]


def default_settings(provider: ProviderName = "openai") -> Settings:
    """Pristine defaults for a provider, ignoring env vars and the config file."""
    return Settings.model_construct(
        provider=provider,
        model=PROVIDERS[provider].default_model,
        base_url="",
        web_search=True,
        api_key_storage="keyring",
        api_key="",
    )


def load_settings() -> Settings | None:
    """Return saved settings, or None when unconfigured (first launch)."""
    if not config_path().is_file():
        return None
    try:
        return Settings()
    except ValidationError:
        return None


def save_settings(settings: Settings) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump()
    if settings.api_key_storage != "config":
        data.pop("api_key")
    path.write_text(tomli_w.dumps(data))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # keys may be stored here on keyring fallback
    return path


def store_api_key(settings: Settings, key: str) -> Settings:
    """Store the key in the system keyring, falling back to the config file.

    Returns the settings adjusted to reflect where the key actually lives;
    the caller is responsible for save_settings().
    """
    import keyring
    import keyring.errors

    try:
        keyring.set_password(KEYRING_SERVICE, settings.provider, key)
        return settings.model_copy(update={"api_key_storage": "keyring", "api_key": ""})
    except keyring.errors.KeyringError:
        return settings.model_copy(update={"api_key_storage": "config", "api_key": key})


def get_api_key(settings: Settings) -> str:
    """Resolve the API key: keyring, then config/$SPECFILL_API_KEY, then provider env var."""
    if settings.api_key_storage == "keyring":
        import keyring
        import keyring.errors

        try:
            stored = keyring.get_password(KEYRING_SERVICE, settings.provider)
            if stored:
                return stored
        except keyring.errors.KeyringError:
            pass
    if settings.api_key:
        return settings.api_key
    return os.environ.get(settings.preset.env_var, "")


def has_api_key(settings: Settings) -> bool:
    return bool(get_api_key(settings))
