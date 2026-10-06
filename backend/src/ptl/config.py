"""Application settings, read from environment variables and the repo-root `.env` file."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ptl.safety import ALPACA_PAPER_BASE_URL

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    @property
    def broker_credentials_configured(self) -> bool:
        """True when both keys are present. Says nothing about whether they are valid."""
        return bool(
            self.alpaca_api_key_id
            and self.alpaca_api_key_id.get_secret_value()
            and self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value()
        )
