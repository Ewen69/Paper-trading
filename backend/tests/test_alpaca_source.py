"""Alpaca adapter tests. The Alpaca clients are replaced by in-test fakes; no network calls."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import requests
from alpaca.common.exceptions import APIError
from alpaca.data.models import Quote
from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AccountStatus
from alpaca.trading.models import TradeAccount

from ptl.config import Settings
from ptl.data.alpaca_source import AlpacaQuoteSource, make_paper_trading_client
from ptl.data.live import InvalidSymbolError, NoQuoteError, NotConfiguredError, QuoteSourceError
from ptl.provenance import DataType
from ptl.safety import LiveTradingRefusedError
from tests.conftest import MARKET_OPEN_NOW, SettingsFactory

KEYS = {"alpaca_api_key_id": "test-key", "alpaca_api_secret_key": "test-secret"}


def _quote(symbol: str, bid: float = 600.01, ask: float = 600.03) -> Quote:
    raw = {"t": "2025-07-07T14:59:58Z", "bp": bid, "bs": 3, "ap": ask, "as": 2, "c": ["R"]}
    return Quote(symbol, raw)


class FakeStockClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.requests: list[StockLatestQuoteRequest] = []

    def get_stock_latest_quote(
        self, request_params: StockLatestQuoteRequest
    ) -> dict[str, Quote] | dict[str, Any]:
        self.requests.append(request_params)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeOptionClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.requests: list[OptionLatestQuoteRequest] = []

    def get_option_latest_quote(
        self, request_params: OptionLatestQuoteRequest
    ) -> dict[str, Quote] | dict[str, Any]:
        self.requests.append(request_params)
        return self.result


class FakeAccountClient:
    def __init__(self, result: TradeAccount | Exception) -> None:
        self.result = result
        self.calls = 0

    def get_account(self) -> TradeAccount | dict[str, Any]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _account() -> TradeAccount:
    return TradeAccount(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        account_number="PA0000000",
        status=AccountStatus.ACTIVE,
        cash="100000",
    )


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _source(
    settings: Settings,
    stock: FakeStockClient | None = None,
    option: FakeOptionClient | None = None,
    account: FakeAccountClient | None = None,
    clock: Clock | None = None,
) -> AlpacaQuoteSource:
    return AlpacaQuoteSource(
        settings,
        clock or Clock(MARKET_OPEN_NOW),
        stock_client=stock,
        option_client=option,
        account_client=account,
    )


def test_stock_quote_is_mapped_with_feed(make_settings: SettingsFactory) -> None:
    stock = FakeStockClient({"SPY": _quote("SPY")})
    source = _source(make_settings(**KEYS), stock=stock)
    quote = source.latest_stock_quote(" spy ")
    assert (quote.symbol, quote.bid, quote.ask, quote.bid_size, quote.ask_size) == (
        "SPY",
        600.01,
        600.03,
        3,
        2,
    )
    assert quote.timestamp == datetime(2025, 7, 7, 14, 59, 58, tzinfo=UTC)
    assert stock.requests[0].feed is not None
    assert stock.requests[0].feed.value == "iex"
    assert source.stock_feed.data_type is DataType.REAL_TIME


def test_option_quote_uses_indicative_feed_and_is_delayed(make_settings: SettingsFactory) -> None:
    occ = "SPY251219C00600000"
    option = FakeOptionClient({occ: _quote(occ, 12.1, 12.4)})
    source = _source(make_settings(**KEYS), option=option)
    assert source.latest_option_quote(occ.lower()).ask == 12.4
    assert option.requests[0].feed is not None
    assert option.requests[0].feed.value == "indicative"
    assert source.option_feed.data_type is DataType.DELAYED


def test_missing_quote_is_no_data_not_a_guess(make_settings: SettingsFactory) -> None:
    source = _source(make_settings(**KEYS), stock=FakeStockClient({}))
    with pytest.raises(NoQuoteError):
        source.latest_stock_quote("SPY")


@pytest.mark.parametrize("symbol", ["", "SPY; DROP", "../etc", "TOOLONGSYMBOL1"])
def test_invalid_symbols_never_reach_the_client(
    make_settings: SettingsFactory, symbol: str
) -> None:
    stock = FakeStockClient({})
    with pytest.raises(InvalidSymbolError):
        _source(make_settings(**KEYS), stock=stock).latest_stock_quote(symbol)
    assert stock.requests == []


def test_invalid_occ_symbol(make_settings: SettingsFactory) -> None:
    with pytest.raises(InvalidSymbolError):
        _source(make_settings(**KEYS), option=FakeOptionClient({})).latest_option_quote("SPY")


def test_api_errors_are_translated(make_settings: SettingsFactory) -> None:
    response = requests.Response()
    response.status_code = 403
    error = APIError('{"message": "forbidden"}', requests.HTTPError(response=response))  # type: ignore[no-untyped-call]
    source = _source(make_settings(**KEYS), stock=FakeStockClient(error))
    with pytest.raises(QuoteSourceError, match="HTTP 403"):
        source.latest_stock_quote("SPY")


def test_network_errors_are_translated(make_settings: SettingsFactory) -> None:
    source = _source(make_settings(**KEYS), stock=FakeStockClient(requests.ConnectionError()))
    with pytest.raises(QuoteSourceError, match="Could not reach Alpaca"):
        source.latest_stock_quote("SPY")


def test_without_keys_quotes_are_unavailable(make_settings: SettingsFactory) -> None:
    source = AlpacaQuoteSource(make_settings(), Clock(MARKET_OPEN_NOW))
    with pytest.raises(NotConfiguredError):
        source.latest_stock_quote("SPY")
    status = source.status()
    assert (status.configured, status.reachable) == (False, None)


def test_status_verifies_account_and_is_cached(make_settings: SettingsFactory) -> None:
    clock = Clock(MARKET_OPEN_NOW)
    account = FakeAccountClient(_account())
    source = _source(make_settings(**KEYS), account=account, clock=clock)
    status = source.status()
    assert (status.reachable, status.account_status, status.error) == (True, "ACTIVE", None)
    source.status()
    assert account.calls == 1
    clock.now += timedelta(seconds=61)
    source.status()
    assert account.calls == 2


def test_status_reports_unreachable(make_settings: SettingsFactory) -> None:
    account = FakeAccountClient(requests.ConnectionError())
    status = _source(make_settings(**KEYS), account=account).status()
    assert status.reachable is False
    assert status.error is not None


def test_trading_client_is_built_for_paper_only(make_settings: SettingsFactory) -> None:
    client = make_paper_trading_client(make_settings(**KEYS))
    assert isinstance(client, TradingClient)
    assert "paper-api.alpaca.markets" in str(getattr(client, "_base_url", ""))


def test_trading_client_refuses_live_url(make_settings: SettingsFactory) -> None:
    settings = make_settings(alpaca_base_url="https://evil.example", **KEYS)
    with pytest.raises(LiveTradingRefusedError):
        make_paper_trading_client(settings)


def test_trading_client_requires_keys(make_settings: SettingsFactory) -> None:
    with pytest.raises(NotConfiguredError):
        make_paper_trading_client(make_settings())
