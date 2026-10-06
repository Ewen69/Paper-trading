from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ptl import __version__
from ptl.app import create_app
from ptl.safety import ALPACA_PAPER_BASE_URL, LiveTradingRefusedError
from tests.conftest import SettingsFactory


def test_refuses_to_start_with_live_endpoint(make_settings: SettingsFactory) -> None:
    with pytest.raises(LiveTradingRefusedError):
        create_app(make_settings(alpaca_base_url="https://api.alpaca.markets"))


def test_refuses_to_start_when_env_points_at_live(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(LiveTradingRefusedError):
        create_app(make_settings())


def test_health_reports_paper_mode_with_provenance(make_settings: SettingsFactory) -> None:
    client = TestClient(create_app(make_settings()))
    before = datetime.now(UTC)
    response = client.get("/health")
    after = datetime.now(UTC)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["trading_mode"] == "paper"
    assert body["broker_base_url"] == ALPACA_PAPER_BASE_URL
    assert body["broker_credentials_configured"] is False
    assert body["source"] == "backend server clock"
    as_of = datetime.fromisoformat(body["as_of"])
    assert as_of.tzinfo is not None
    assert before - timedelta(seconds=1) <= as_of <= after + timedelta(seconds=1)


def test_health_never_exposes_secrets(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        alpaca_api_key_id="KEY-SHOULD-NOT-LEAK", alpaca_api_secret_key="SECRET-SHOULD-NOT-LEAK"
    )
    response = TestClient(create_app(settings)).get("/health")
    assert response.json()["broker_credentials_configured"] is True
    assert "SHOULD-NOT-LEAK" not in response.text
