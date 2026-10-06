"""Alpaca implementation of `QuoteSource`, plus the only constructor for a trading client.

Phase 1 uses the trading client read-only (account status, to verify the paper keys).
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Protocol

import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.enums import OptionsFeed as AlpacaOptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.models import Quote
from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.models import TradeAccount

from ptl.config import Settings
from ptl.data.live import (
    Clock,
    FeedInfo,
    NoQuoteError,
    NotConfiguredError,
    QuoteSourceError,
    SourceStatus,
    normalize_occ_symbol,
    normalize_stock_symbol,
)
from ptl.data.models import LiveQuote
from ptl.provenance import DataType
from ptl.safety import assert_paper_endpoint

_STOCK_FEEDS: dict[str, tuple[DataFeed, FeedInfo]] = {
    "iex": (
        DataFeed.IEX,
        FeedInfo(
            "Alpaca IEX",
            DataType.REAL_TIME,
            "Real-time, but from the IEX exchange only (a few % of US volume). Quotes can be "
            "wider than the national best bid/offer.",
        ),
    ),
    "sip": (
        DataFeed.SIP,
        FeedInfo(
            "Alpaca SIP",
            DataType.REAL_TIME,
            "Consolidated national best bid/offer. Needs a paid subscription.",
        ),
    ),
    "delayed_sip": (
        DataFeed.DELAYED_SIP,
        FeedInfo("Alpaca SIP (15-min delayed)", DataType.DELAYED, "Consolidated, delayed 15 min."),
    ),
}
_OPTION_FEEDS: dict[str, tuple[AlpacaOptionsFeed, FeedInfo]] = {
    "indicative": (
        AlpacaOptionsFeed.INDICATIVE,
        FeedInfo(
            "Alpaca options (indicative)",
            DataType.DELAYED,
            "Free indicative feed: delayed and modified from OPRA. Not suitable for fill "
            "decisions.",
        ),
    ),
    "opra": (
        AlpacaOptionsFeed.OPRA,
        FeedInfo("Alpaca OPRA", DataType.REAL_TIME, "Real-time OPRA. Needs a paid subscription."),
    ),
}


class _StockQuoteClient(Protocol):
    def get_stock_latest_quote(
        self, request_params: StockLatestQuoteRequest
    ) -> dict[str, Quote] | dict[str, Any]: ...


class _OptionQuoteClient(Protocol):
    def get_option_latest_quote(
        self, request_params: OptionLatestQuoteRequest
    ) -> dict[str, Quote] | dict[str, Any]: ...


class _AccountClient(Protocol):
    def get_account(self) -> TradeAccount | dict[str, Any]: ...


def make_paper_trading_client(settings: Settings) -> TradingClient:
    """Build a TradingClient that can only talk to the paper endpoint, then re-verify it."""
    if not (settings.alpaca_api_key_id and settings.alpaca_api_secret_key):
        raise NotConfiguredError("Alpaca paper API keys are not configured in .env")
    paper_url = assert_paper_endpoint(settings.alpaca_base_url)
    client = TradingClient(
        api_key=settings.alpaca_api_key_id.get_secret_value(),
        secret_key=settings.alpaca_api_secret_key.get_secret_value(),
        paper=True,
        url_override=paper_url,
    )
    base: object = getattr(client, "_base_url", None)
    assert_paper_endpoint(str(base.value if isinstance(base, Enum) else base))
    return client


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, APIError):
        status = exc.status_code
        if status in (401, 403):
            return f"Alpaca rejected the credentials or the feed is not in your plan (HTTP {status})."
        return f"Alpaca API error (HTTP {status})."
    return f"Could not reach Alpaca ({type(exc).__name__})."


class AlpacaQuoteSource:
    name = "Alpaca"

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        *,
        stock_client: _StockQuoteClient | None = None,
        option_client: _OptionQuoteClient | None = None,
        account_client: _AccountClient | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._stock_feed, self._stock_info = _STOCK_FEEDS[settings.alpaca_stock_feed]
        self._option_feed, self._option_info = _OPTION_FEEDS[settings.alpaca_options_feed]
        self._configured = settings.broker_credentials_configured
        self._stock_client = stock_client
        self._option_client = option_client
        self._account_client = account_client
        self._cached_status: SourceStatus | None = None

    @property
    def stock_feed(self) -> FeedInfo:
        return self._stock_info

    @property
    def option_feed(self) -> FeedInfo:
        return self._option_info

    def _keys(self) -> tuple[str, str]:
        key, secret = self._settings.alpaca_api_key_id, self._settings.alpaca_api_secret_key
        if not self._settings.broker_credentials_configured or key is None or secret is None:
            raise NotConfiguredError("Alpaca paper API keys are not configured in .env")
        return key.get_secret_value(), secret.get_secret_value()

    def _stocks(self) -> _StockQuoteClient:
        if self._stock_client is None:
            self._stock_client = StockHistoricalDataClient(*self._keys())
        return self._stock_client

    def _options(self) -> _OptionQuoteClient:
        if self._option_client is None:
            self._option_client = OptionHistoricalDataClient(*self._keys())
        return self._option_client

    def _account(self) -> _AccountClient:
        if self._account_client is None:
            self._account_client = make_paper_trading_client(self._settings)
        return self._account_client

    @staticmethod
    def _to_live(symbol: str, result: dict[str, Quote] | dict[str, Any]) -> LiveQuote:
        quote = result.get(symbol)
        if not isinstance(quote, Quote):
            raise NoQuoteError(f"Alpaca returned no quote for {symbol}")
        return LiveQuote(
            symbol=symbol,
            bid=float(quote.bid_price),
            ask=float(quote.ask_price),
            bid_size=float(quote.bid_size),
            ask_size=float(quote.ask_size),
            timestamp=quote.timestamp,
        )

    def latest_stock_quote(self, symbol: str) -> LiveQuote:
        symbol = normalize_stock_symbol(symbol)
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._stock_feed)
        try:
            result = self._stocks().get_stock_latest_quote(request)
        except (APIError, requests.RequestException) as exc:
            raise QuoteSourceError(_describe_error(exc)) from exc
        return self._to_live(symbol, result)

    def latest_option_quote(self, occ_symbol: str) -> LiveQuote:
        symbol = normalize_occ_symbol(occ_symbol)
        request = OptionLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._option_feed)
        try:
            result = self._options().get_option_latest_quote(request)
        except (APIError, requests.RequestException) as exc:
            raise QuoteSourceError(_describe_error(exc)) from exc
        return self._to_live(symbol, result)

    def status(self) -> SourceStatus:
        now = self._clock()
        cache_for = timedelta(seconds=self._settings.broker_status_cache_seconds)
        if self._cached_status and now - self._cached_status.checked_at < cache_for:
            return self._cached_status
        self._cached_status = self._check(now)
        return self._cached_status

    def _check(self, now: datetime) -> SourceStatus:
        def status(
            reachable: bool | None, account: str | None, error: str | None
        ) -> SourceStatus:
            return SourceStatus(
                name=self.name,
                configured=self._configured,
                reachable=reachable,
                account_status=account,
                error=error,
                checked_at=now,
                stock_feed=self._stock_info,
                option_feed=self._option_info,
            )

        if not self._configured:
            return status(None, None, "Paper API keys not configured. Add them to .env.")
        try:
            account = self._account().get_account()
        except (APIError, requests.RequestException) as exc:
            return status(False, None, _describe_error(exc))
        if not isinstance(account, TradeAccount):
            return status(False, None, "Unexpected account response from Alpaca.")
        account_status = account.status.value if account.status else "unknown"
        return status(True, str(account_status), None)
