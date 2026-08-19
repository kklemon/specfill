import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep tests away from the user's real config, env vars, and keyring."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in (
        "SPECFILL_PROVIDER",
        "SPECFILL_MODEL",
        "SPECFILL_BASE_URL",
        "SPECFILL_WEB_SEARCH",
        "SPECFILL_API_KEY_STORAGE",
        "SPECFILL_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        keyring, "set_password", lambda svc, user, pw: store.__setitem__((svc, user), pw)
    )
    monkeypatch.setattr(keyring, "get_password", lambda svc, user: store.get((svc, user)))
    yield store
