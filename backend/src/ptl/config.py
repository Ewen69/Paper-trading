"""Application settings, read from environment variables and the repo-root `.env` file."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ptl.safety import ALPACA_PAPER_BASE_URL

REPO_ROOT = Path(__file__).resolve().parents[3]

StockFeed = Literal["iex", "sip", "delayed_sip"]
OptionsFeed = Literal["indicative", "opra"]


class Settings(BaseSettings):
    """Runtime configuration. Secrets are `SecretStr` so they never appear in reprs or logs."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    alpaca_base_url: str = ALPACA_PAPER_BASE_URL
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    # Free Alpaca accounts get real-time IEX (a single venue) for stocks and the delayed
    # "indicative" feed for options. SIP / OPRA need a paid market-data subscription.
    alpaca_stock_feed: StockFeed = "iex"
    alpaca_options_feed: OptionsFeed = "indicative"

    database_path: Path = REPO_ROOT / "data" / "ptl.sqlite3"

    # Staleness thresholds for live quotes, by data type.
    real_time_max_age_seconds: int = Field(default=120, gt=0)
    delayed_max_age_seconds: int = Field(default=30 * 60, gt=0)
    # A historical dataset is stale when its last date is this many sessions behind.
    dataset_stale_after_sessions: int = Field(default=5, ge=0)
    # Cache for the broker reachability check so Data Health doesn't hit Alpaca every refresh.
    broker_status_cache_seconds: int = Field(default=60, ge=0)

    # Risk limits, enforced in code on every paper order (see ptl/risk/engine.py).
    risk_max_loss_per_trade: float = Field(default=1_000.0, gt=0)
    risk_max_daily_loss: float = Field(default=2_000.0, gt=0)
    risk_max_open_positions: int = Field(default=5, ge=1)
    risk_max_capital_at_risk_pct: float = Field(default=0.5, gt=0, le=1)

    # Agents: background research workers (in-sample sweeps only) and the paper runner.
    agents_autopilot: bool = True
    agents_poll_seconds: float = Field(default=30.0, gt=0)
    agents_max_combinations: int = Field(default=50, ge=1)
    paper_dry_run_capital: float = Field(default=100_000.0, gt=0)
    # Net worth: a balance older than this many days is flagged stale.
    networth_stale_after_days: int = Field(default=45, ge=1)
    # Portfolio: default benchmark and risk window (sessions) for volatility, drawdown and beta.
    portfolio_benchmark: str = "SPY"
    portfolio_window_sessions: int = Field(default=252, ge=20, le=2520)

    @field_validator("database_path")
    @classmethod
    def _anchor_to_repo(cls, path: Path) -> Path:
        """Relative paths are relative to the repo root, wherever the command runs from."""
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def broker_credentials_configured(self) -> bool:
        """True when both keys are present. Says nothing about whether they are valid."""
        return bool(
            self.alpaca_api_key_id
            and self.alpaca_api_key_id.get_secret_value()
            and self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value()
        )
