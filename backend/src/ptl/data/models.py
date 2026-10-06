"""Plain row types shared by ingestion, quality checks, and storage."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

OptionType = Literal["call", "put"]
ExerciseStyle = Literal["american", "european"]


class DatasetKind(StrEnum):
    EQUITY_BARS = "equity_bars"
    OPTION_QUOTES = "option_quotes"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class EquityBar:
    symbol: str
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float | None = None


@dataclass(frozen=True, slots=True)
class OptionQuote:
    underlying: str
    quote_date: date
    expiration: date
    strike: float
    option_type: OptionType
    bid: float
    ask: float
    root: str = ""
    exercise_style: ExerciseStyle | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    underlying_price: float | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    @property
    def contract_key(self) -> tuple[str, str, date, float, OptionType]:
        return (self.underlying, self.root, self.expiration, self.strike, self.option_type)


@dataclass(frozen=True, slots=True)
class LiveQuote:
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class QualityIssue:
    """One finding, aggregated over all rows of one symbol that triggered it."""

    check: str
    severity: Severity
    symbol: str
    count: int
    first_date: date
    last_date: date
    detail: str
