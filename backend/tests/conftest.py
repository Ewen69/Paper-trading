from collections.abc import Callable

import pytest

from ptl.config import Settings

_ALPACA_ENV_VARS = ("ALPACA_BASE_URL", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY")

type SettingsFactory = Callable[..., Settings]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's shell environment out of tests."""
    for name in _ALPACA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Build Settings without reading the real `.env` file."""

    def _make(**overrides: object) -> Settings:
        return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]

    return _make
