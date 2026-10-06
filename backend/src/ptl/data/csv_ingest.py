"""CSV ingestion for historical data. See docs/DATA.md for the column contracts.

A file is either imported completely or rejected completely, with line-numbered errors. Parse
errors (missing columns, unparseable values, duplicate keys) reject the file. Quality problems
(crossed markets, gaps) do not; they are real properties of the data, so they are stored and
flagged for the backtester to respect.
"""

import csv
import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import astuple, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from ptl.data import repository
from ptl.data.models import (
    DatasetKind,
    EquityBar,
    ExerciseStyle,
    OptionQuote,
    OptionType,
    QualityIssue,
)
from ptl.data.quality import check_equity_bars, check_option_quotes
from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType

DEFAULT_DATE_FORMAT = "%Y-%m-%d"
MAX_REPORTED_ERRORS = 50
ALLOWED_DATA_TYPES = frozenset({DataType.END_OF_DAY, DataType.MANUAL})
_MISSING = frozenset({"", "na", "n/a", "nan", "null", "none", "-"})
_SYMBOL = re.compile(r"^[A-Z0-9^][A-Z0-9.\-/^]{0,19}$")

EQUITY_REQUIRED = ("symbol", "date", "open", "high", "low", "close", "volume")
EQUITY_OPTIONAL = ("adj_close",)
OPTION_REQUIRED = (
    "underlying",
    "quote_date",
    "expiration",
    "strike",
    "option_type",
    "bid",
    "ask",
)
OPTION_OPTIONAL = (
    "root",
    "exercise_style",
    "bid_size",
    "ask_size",
    "last",
    "volume",
    "open_interest",
    "underlying_price",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
)

T = TypeVar("T")


class ImportRejectedError(Exception):
    def __init__(self, errors: list[str], total_errors: int | None = None) -> None:
        self.errors = errors
        self.total_errors = total_errors if total_errors is not None else len(errors)
        shown = "\n  ".join(errors)
        super().__init__(f"Import rejected ({self.total_errors} error(s)):\n  {shown}")


@dataclass(frozen=True, slots=True)
class ImportResult:
    dataset_id: int
    already_imported: bool
    row_count: int
    issues: list[QualityIssue]


class _FieldError(ValueError):
    pass


class _Row:
    """Typed accessors over one CSV row; each failure raises with the column name."""

    def __init__(self, values: Mapping[str, str], date_format: str) -> None:
        self._values = values
        self._date_format = date_format

    def _raw(self, name: str) -> str | None:
        value = (self._values.get(name) or "").strip()
        return None if value.lower() in _MISSING else value

    def _required(self, name: str) -> str:
        value = self._raw(name)
        if value is None:
            raise _FieldError(f"{name} is empty")
        return value

    def _parse(self, name: str, raw: str, parse: Callable[[str], T], kind: str) -> T:
        try:
            return parse(raw)
        except ValueError:
            raise _FieldError(f"{name}={raw!r} is not a valid {kind}") from None

    def symbol(self, name: str) -> str:
        value = self._required(name).upper()
        if not _SYMBOL.match(value):
            raise _FieldError(f"{name}={value!r} is not a valid symbol")
        return value

    def number(self, name: str) -> float:
        value = self._parse(name, self._required(name), float, "number")
        if value != value or value in (float("inf"), float("-inf")):
            raise _FieldError(f"{name} is not finite")
        return value

    def optional_number(self, name: str) -> float | None:
        return None if self._raw(name) is None else self.number(name)

    def integer(self, name: str) -> int:
        value = self.number(name)
        if not value.is_integer():
            raise _FieldError(f"{name}={value} is not a whole number")
        return int(value)

    def optional_integer(self, name: str) -> int | None:
        return None if self._raw(name) is None else self.integer(name)

    def day(self, name: str) -> date:
        raw = self._required(name)
        return self._parse(
            name, raw, lambda v: datetime.strptime(v, self._date_format).date(), "date"
        )

    def option_type(self, name: str) -> OptionType:
        value = self._required(name).lower()
        if value in ("c", "call"):
            return "call"
        if value in ("p", "put"):
            return "put"
        raise _FieldError(f"{name}={value!r} must be call/put/C/P")

    def exercise_style(self, name: str) -> ExerciseStyle | None:
        value = self._raw(name)
        if value is None:
            return None
        lowered = value.lower()
        if lowered == "american":
            return "american"
        if lowered == "european":
            return "european"
        raise _FieldError(f"{name}={value!r} must be american or european")

    def text(self, name: str) -> str:
        return (self._raw(name) or "").upper()


def _rows(
    path: Path,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    column_map: Mapping[str, str],
    date_format: str,
) -> Iterator[tuple[int, _Row]]:
    """Yield (line number, row) with headers normalized and renamed to canonical names."""
    unknown = set(column_map) - set(required) - set(optional)
    if unknown:
        raise ImportRejectedError([f"--map names unknown column(s): {sorted(unknown)}"])
    rename = {source.strip().lower(): canonical for canonical, source in column_map.items()}

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None or not any(h.strip() for h in header):
            raise ImportRejectedError(["file is empty"])
        names = [rename.get(h.strip().lower(), h.strip().lower()) for h in header]
        missing = [c for c in required if c not in names]
        if missing:
            raise ImportRejectedError(
                [f"missing required column(s) {missing}; found {names}. Use --map to rename."]
            )
        for values in reader:
            if not any(v.strip() for v in values):
                continue
            yield reader.line_num, _Row(dict(zip(names, values, strict=False)), date_format)


def _collect(
    rows: Iterator[tuple[int, _Row]],
    build: Callable[[_Row], T],
    key: Callable[[T], tuple[object, ...]],
) -> list[T]:
    parsed: list[T] = []
    errors: list[str] = []
    total_errors = 0
    seen: dict[tuple[object, ...], int] = {}
    for line, row in rows:
        try:
            item = build(row)
        except _FieldError as exc:
            total_errors += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"line {line}: {exc}")
            continue
        k = key(item)
        if k in seen:
            total_errors += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"line {line}: duplicate of line {seen[k]} for key {k}")
            continue
        seen[k] = line
        parsed.append(item)
    if errors:
        raise ImportRejectedError(errors, total_errors)
    if not parsed:
        raise ImportRejectedError(["file has a header but no data rows"])
    return parsed


def parse_equity_bars(
    path: Path,
    column_map: Mapping[str, str] | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
) -> list[EquityBar]:
    def build(r: _Row) -> EquityBar:
        return EquityBar(
            symbol=r.symbol("symbol"),
            session_date=r.day("date"),
            open=r.number("open"),
            high=r.number("high"),
            low=r.number("low"),
            close=r.number("close"),
            volume=r.integer("volume"),
            adj_close=r.optional_number("adj_close"),
        )

    rows = _rows(path, EQUITY_REQUIRED, EQUITY_OPTIONAL, column_map or {}, date_format)
    return _collect(rows, build, lambda b: (b.symbol, b.session_date))


def parse_option_quotes(
    path: Path,
    column_map: Mapping[str, str] | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
) -> list[OptionQuote]:
    def build(r: _Row) -> OptionQuote:
        return OptionQuote(
            underlying=r.symbol("underlying"),
            quote_date=r.day("quote_date"),
            expiration=r.day("expiration"),
            strike=r.number("strike"),
            option_type=r.option_type("option_type"),
            bid=r.number("bid"),
            ask=r.number("ask"),
            root=r.text("root"),
            exercise_style=r.exercise_style("exercise_style"),
            bid_size=r.optional_integer("bid_size"),
            ask_size=r.optional_integer("ask_size"),
            last=r.optional_number("last"),
            volume=r.optional_integer("volume"),
            open_interest=r.optional_integer("open_interest"),
            underlying_price=r.optional_number("underlying_price"),
            implied_volatility=r.optional_number("implied_volatility"),
            delta=r.optional_number("delta"),
            gamma=r.optional_number("gamma"),
            theta=r.optional_number("theta"),
            vega=r.optional_number("vega"),
        )

    rows = _rows(path, OPTION_REQUIRED, OPTION_OPTIONAL, column_map or {}, date_format)
    return _collect(rows, build, lambda q: (*q.contract_key, q.quote_date))


def import_csv(  # noqa: PLR0913
    conn: sqlite3.Connection,
    path: Path,
    kind: DatasetKind,
    source: str,
    calendar: MarketCalendar,
    now: datetime,
    data_type: DataType = DataType.END_OF_DAY,
    column_map: Mapping[str, str] | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
) -> ImportResult:
    """Validate, quality-check, and store one CSV file as a new dataset (idempotent by hash)."""
    source = source.strip()
    if not source:
        raise ImportRejectedError(["source is required (where did this data come from?)"])
    if data_type not in ALLOWED_DATA_TYPES:
        allowed = sorted(str(t) for t in ALLOWED_DATA_TYPES)
        raise ImportRejectedError([f"data type {data_type!s} is not valid for CSV; use {allowed}"])

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    existing = repository.find_dataset_by_hash(conn, digest)
    if existing is not None:
        return ImportResult(
            existing.id, True, existing.row_count, repository.issues_for(conn, existing.id)
        )

    sql: str
    values: list[tuple[object, ...]]
    if kind is DatasetKind.EQUITY_BARS:
        bars = parse_equity_bars(path, column_map, date_format)
        issues = check_equity_bars(bars, calendar)
        keys = [(b.symbol, b.session_date) for b in bars]
        sql = f"INSERT INTO equity_bars VALUES ({', '.join('?' * 9)})"
        values = [_storable(astuple(b)) for b in bars]
    else:
        quotes = parse_option_quotes(path, column_map, date_format)
        issues = check_option_quotes(quotes, calendar)
        keys = [(q.underlying, q.quote_date) for q in quotes]
        # Column order matches the OptionQuote field order.
        sql = (
            "INSERT INTO option_quotes (dataset_id, underlying, quote_date, expiration, strike, "
            "option_type, bid, ask, root, exercise_style, bid_size, ask_size, last, volume, "
            "open_interest, underlying_price, implied_volatility, delta, gamma, theta, vega) "
            f"VALUES ({', '.join('?' * 21)})"
        )
        values = [_storable(astuple(q)) for q in quotes]

    meta = repository.NewDataset.describe(kind, keys)
    with conn:
        dataset_id = repository.insert_dataset(
            conn,
            meta,
            source=source,
            data_type=data_type,
            file_name=path.name,
            sha256=digest,
            imported_at=now,
        )
        conn.executemany(sql, ((dataset_id, *row) for row in values))
        repository.insert_issues(conn, dataset_id, issues)
    return ImportResult(dataset_id, False, meta.row_count, issues)


def _storable(row: tuple[object, ...]) -> tuple[object, ...]:
    """Dates become ISO strings (sqlite3's implicit date adapter is deprecated)."""
    return tuple(v.isoformat() if isinstance(v, date) else v for v in row)
