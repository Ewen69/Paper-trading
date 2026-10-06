"""Shared fixtures. Everything here is test-only; fakes never reach code the UI reads from."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ptl.config import Settings
from ptl.market_calendar import MarketCalendar

_ENV_VARS = (
    "ALPACA_BASE_URL",
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
    "ALPACA_STOCK_FEED",
    "ALPACA_OPTIONS_FEED",
    "DATABASE_PATH",
)

type SettingsFactory = Callable[..., Settings]
type CsvWriter = Callable[[str, str], Path]

# Monday 2025-07-07 11:00 New York: the market is open.
MARKET_OPEN_NOW = datetime(2025, 7, 7, 15, 0, tzinfo=UTC)
# Saturday 2025-07-05 16:00 UTC: the market is closed; the last close was Thu 2025-07-03 13:00 ET.
WEEKEND_NOW = datetime(2025, 7, 5, 16, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's shell environment out of tests."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"


@pytest.fixture
def make_settings(db_path: Path) -> SettingsFactory:
    """Build Settings without reading the real `.env`, using a throwaway database."""

    def _make(**overrides: object) -> Settings:
        overrides.setdefault("database_path", db_path)
        return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]

    return _make


@pytest.fixture(scope="session")
def calendar() -> MarketCalendar:
    return MarketCalendar()


@pytest.fixture
def write_csv(tmp_path: Path) -> CsvWriter:
    def _write(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text.strip() + "\n", encoding="utf-8")
        return path

    return _write


FIXTURES = Path(__file__).parent / "fixtures"
