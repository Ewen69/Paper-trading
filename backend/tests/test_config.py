import pytest

from ptl.config import REPO_ROOT
from ptl.safety import ALPACA_PAPER_BASE_URL
from tests.conftest import SettingsFactory


def test_defaults_to_paper_endpoint_without_credentials(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    assert settings.alpaca_base_url == ALPACA_PAPER_BASE_URL
    assert settings.broker_credentials_configured is False


def test_reads_environment(monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory) -> None:
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    assert make_settings().broker_credentials_configured is True


def test_relative_database_path_is_anchored_to_repo(make_settings: SettingsFactory) -> None:
    assert make_settings(database_path="data/x.sqlite3").database_path == (
        REPO_ROOT / "data" / "x.sqlite3"
    )


def test_empty_keys_do_not_count_as_configured(make_settings: SettingsFactory) -> None:
    settings = make_settings(alpaca_api_key_id="", alpaca_api_secret_key="")
    assert settings.broker_credentials_configured is False


def test_secrets_never_appear_in_repr_or_dump(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        alpaca_api_key_id="KEY-SHOULD-NOT-LEAK", alpaca_api_secret_key="SECRET-SHOULD-NOT-LEAK"
    )
    rendered = f"{settings!r} {settings!s} {settings.model_dump()} {settings.model_dump_json()}"
    assert "KEY-SHOULD-NOT-LEAK" not in rendered
    assert "SECRET-SHOULD-NOT-LEAK" not in rendered
