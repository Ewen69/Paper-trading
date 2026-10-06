"""Live quote source interface. Implementations are swappable; the API only sees this."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ptl.data.models import LiveQuote
from ptl.provenance import DataType

Clock = Callable[[], datetime]

STOCK_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


class QuoteSourceError(Exception):
    """The source could not provide a quote. The message is safe to show the user."""


class NotConfiguredError(QuoteSourceError):
    pass


class NoQuoteError(QuoteSourceError):
    pass


class InvalidSymbolError(QuoteSourceError):
    pass


@dataclass(frozen=True, slots=True)
class FeedInfo:
    name: str
    data_type: DataType
    note: str


@dataclass(frozen=True, slots=True)
class SourceStatus:
    name: str
    configured: bool
    reachable: bool | None  # None = not checked (no credentials)
    account_status: str | None
    error: str | None
    checked_at: datetime
    stock_feed: FeedInfo
    option_feed: FeedInfo


class QuoteSource(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def stock_feed(self) -> FeedInfo: ...
    @property
    def option_feed(self) -> FeedInfo: ...
    def latest_stock_quote(self, symbol: str) -> LiveQuote: ...
    def latest_option_quote(self, occ_symbol: str) -> LiveQuote: ...
    def status(self) -> SourceStatus: ...


def normalize_stock_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not STOCK_SYMBOL.match(value):
        raise InvalidSymbolError(f"{symbol!r} is not a valid stock/ETF symbol")
    return value


def normalize_occ_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not OCC_SYMBOL.match(value):
        raise InvalidSymbolError(
            f"{symbol!r} is not an OCC option symbol (e.g. SPY261218C00600000)"
        )
    return value
