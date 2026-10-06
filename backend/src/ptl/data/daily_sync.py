"""Daily equity bar sync from Alpaca market data into local SQLite.

One "sync dataset" per symbol (file name `alpaca-sync:SYMBOL`). The first sync backfills from
SYNC_BACKFILL_START; later syncs append each newly completed session. Rows store raw OHLC plus a
split- and dividend-adjusted close (`adj_close`). If a corporate action changes the adjusted
close of the last stored session, the whole adjusted series is re-fetched so it stays
consistent. Quality checks are re-run after every sync.

Provenance: "Alpaca market data" with the configured stock feed. On the free IEX feed, prices
and volume come from the IEX venue only, not the consolidated tape. Data type: end-of-day.
Only completed sessions are stored, never a partial day.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ptl.backtest import repository as bt_repo
from ptl.config import Settings
from ptl.data import repository
from ptl.data.models import DatasetKind, EquityBar
from ptl.data.quality import check_equity_bars
from ptl.market_calendar import MarketCalendar
from ptl.portfolio import holdings as pf_holdings
from ptl.provenance import DataType

SYNC_PREFIX = "alpaca-sync:"
NEW_YORK = ZoneInfo("America/New_York")
ADJ_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class DailyBar:
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class DailyBarSource(Protocol):
    @property
    def label(self) -> str: ...

    def daily_bars(self, symbol: str, start: date, end: date, *, adjusted: bool) -> list[DailyBar]:
        """Completed daily bars for [start, end], raw or split/dividend-adjusted."""
        ...


class AlpacaDailyBars:
    """Alpaca market data client (read-only; market data, no trading)."""

    def __init__(self, settings: Settings, client: Any = None) -> None:  # noqa: ANN401
        from alpaca.data.historical import StockHistoricalDataClient  # noqa: PLC0415

        from ptl.data.alpaca_source import _STOCK_FEEDS  # noqa: PLC0415

        self._feed, info = _STOCK_FEEDS[settings.alpaca_stock_feed]
        self._label = f"Alpaca market data, {info.name} feed"
        if client is None:
            if not settings.broker_credentials_configured:
                raise ValueError("Alpaca API keys are not set.")
            key, secret = settings.alpaca_api_key_id, settings.alpaca_api_secret_key
            assert key is not None  # noqa: S101 - checked above
            assert secret is not None  # noqa: S101
            client = StockHistoricalDataClient(key.get_secret_value(), secret.get_secret_value())
        self._client = client

    @property
    def label(self) -> str:
        return self._label

    def daily_bars(self, symbol: str, start: date, end: date, *, adjusted: bool) -> list[DailyBar]:
        from alpaca.data.enums import Adjustment  # noqa: PLC0415
        from alpaca.data.requests import StockBarsRequest  # noqa: PLC0415
        from alpaca.data.timeframe import TimeFrame  # noqa: PLC0415

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime(start.year, start.month, start.day, tzinfo=UTC),
            end=datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1),
            adjustment=Adjustment.ALL if adjusted else Adjustment.RAW,
            feed=self._feed,
        )
        result = self._client.get_stock_bars(request)
        rows = getattr(result, "data", {}).get(symbol, [])
        out = []
        for b in rows:
            session = b.timestamp.astimezone(NEW_YORK).date()
            if start <= session <= end:
                out.append(DailyBar(session, b.open, b.high, b.low, b.close, int(b.volume or 0)))
        return out


@dataclass(frozen=True, slots=True)
class SymbolSync:
    symbol: str
    dataset_id: int | None
    added: int
    through: date | None
    created: bool
    adjusted_refresh: bool
    note: str


def sync_dataset_id(conn: sqlite3.Connection, symbol: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM datasets WHERE sha256 = ?", (f"{SYNC_PREFIX}{symbol}",)
    ).fetchone()
    return int(row["id"]) if row else None


def symbols_to_sync(conn: sqlite3.Connection, settings: Settings) -> list[str]:
    """Every equity symbol the app uses: imported bars, holdings, Active Bests, benchmark."""
    symbols = {e.symbol for e in bt_repo.equity_universe(conn)}
    symbols |= {h.symbol for h in pf_holdings.holdings(conn) if h.asset_class in ("stock", "etf")}
    symbols |= {
        h.symbol for h in pf_holdings.holdings(conn) if h.asset_class == "option"
    }  # option underlyings
    rows = conn.execute("SELECT DISTINCT symbol FROM optimizer_active_best")
    symbols |= {r["symbol"] for r in rows}
    symbols.add(settings.portfolio_benchmark.upper())
    return sorted(s for s in symbols if s and s.replace(".", "").isalnum())[
        : settings.sync_max_symbols
    ]


def target_session(now: datetime, calendar: MarketCalendar, delay_minutes: int) -> date:
    """The newest session whose daily bar should exist by now (close + a settling delay)."""
    return calendar.last_completed_session(now - timedelta(minutes=delay_minutes))


def _bars(conn: sqlite3.Connection, dataset_id: int, symbol: str) -> list[EquityBar]:
    return bt_repo.load_equity_bars(conn, dataset_id, symbol)


def _join(
    raw: Sequence[DailyBar], adj: Sequence[DailyBar], symbol: str, sessions: set[date]
) -> list[EquityBar]:
    adjusted = {b.session: b.close for b in adj}
    return [
        EquityBar(
            symbol, b.session, b.open, b.high, b.low, b.close, b.volume, adjusted.get(b.session)
        )
        for b in raw
        if b.session in sessions
    ]


def sync_symbol(  # noqa: PLR0913
    conn: sqlite3.Connection,
    symbol: str,
    *,
    source: DailyBarSource,
    settings: Settings,
    calendar: MarketCalendar,
    now: datetime,
) -> SymbolSync:
    end = target_session(now, calendar, settings.sync_delay_minutes)
    dataset_id = sync_dataset_id(conn, symbol)
    existing = _bars(conn, dataset_id, symbol) if dataset_id else []
    start = (
        existing[-1].session_date + timedelta(days=1) if existing else settings.sync_backfill_start
    )
    if start > end:
        return SymbolSync(symbol, dataset_id, 0, end, False, False, "already current")
    sessions = set(calendar.sessions(start, end))
    raw = source.daily_bars(symbol, start, end, adjusted=False)
    adj = source.daily_bars(symbol, start, end, adjusted=True)
    new = _join(raw, adj, symbol, sessions)
    if not new:
        last_date = existing[-1].session_date if existing else None
        return SymbolSync(
            symbol, dataset_id, 0, last_date, False, False, f"no bars yet through {end}"
        )

    refreshed = False
    with conn:
        if dataset_id is None:
            meta = repository.NewDataset.describe(
                DatasetKind.EQUITY_BARS, [(b.symbol, b.session_date) for b in new]
            )
            dataset_id = repository.insert_dataset(
                conn,
                meta,
                source=f"{source.label}; daily sync",
                data_type=DataType.END_OF_DAY,
                file_name=f"{SYNC_PREFIX}{symbol}",
                sha256=f"{SYNC_PREFIX}{symbol}",
                imported_at=now,
            )
            created = True
        else:
            created = False
            # A split or dividend since the last sync changes past adjusted closes.
            last = existing[-1]
            check = source.daily_bars(symbol, last.session_date, last.session_date, adjusted=True)
            if (
                check
                and last.adj_close is not None
                and abs(check[0].close - last.adj_close) > ADJ_TOLERANCE * max(1.0, last.adj_close)
            ):
                history = source.daily_bars(
                    symbol, existing[0].session_date, last.session_date, adjusted=True
                )
                conn.executemany(
                    "UPDATE equity_bars SET adj_close = ? WHERE dataset_id = ? AND symbol = ? "
                    "AND session_date = ?",
                    [(b.close, dataset_id, symbol, b.session.isoformat()) for b in history],
                )
                refreshed = True
        conn.executemany(
            "INSERT OR IGNORE INTO equity_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    dataset_id,
                    b.symbol,
                    b.session_date.isoformat(),
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.adj_close,
                )
                for b in new
            ],
        )
        everything = _bars(conn, dataset_id, symbol)
        conn.execute(
            "UPDATE datasets SET row_count = ?, coverage_start = ?, coverage_end = ?, "
            "imported_at = ? WHERE id = ?",
            (
                len(everything),
                everything[0].session_date.isoformat(),
                everything[-1].session_date.isoformat(),
                now.isoformat(),
                dataset_id,
            ),
        )
        conn.execute("DELETE FROM quality_issues WHERE dataset_id = ?", (dataset_id,))
        repository.insert_issues(conn, dataset_id, check_equity_bars(everything, calendar))
    return SymbolSync(
        symbol,
        dataset_id,
        len(new),
        new[-1].session_date,
        created,
        refreshed,
        "backfilled" if created else "appended",
    )


def freshest_dataset(conn: sqlite3.Connection, symbol: str) -> int | None:
    """The equity dataset with the newest bar for this symbol (the sync dataset once it exists)."""
    row = conn.execute(
        "SELECT dataset_id, MAX(session_date) AS last FROM equity_bars WHERE symbol = ? "
        "GROUP BY dataset_id ORDER BY last DESC, dataset_id DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return int(row["dataset_id"]) if row else None
