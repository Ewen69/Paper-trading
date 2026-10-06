"""Backtest service: the rigor rules. Price series here are synthetic, test-only fixtures."""

import math
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ptl.backtest import repository as repo
from ptl.backtest import service
from ptl.backtest.models import History
from ptl.backtest.strategies import STRATEGIES, StrategySpec
from ptl.data.csv_ingest import import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar

NOW = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
PriceFn = Callable[[int], float]


def wave(i: int) -> float:
    return 100 + 10 * math.sin(i / 12) + 0.05 * i


def trend(i: int) -> float:
    return 200 + 0.2 * i


def write_bars_csv(  # noqa: PLR0913 - test fixture builder; options are keyword-only
    path: Path,
    calendar: MarketCalendar,
    series: dict[str, PriceFn],
    *,
    sessions: int = 300,
    adjusted: bool = True,
    broken_day: int | None = None,
) -> Path:
    days = calendar.sessions(date(2023, 1, 3), date(2025, 12, 31))[:sessions]
    lines = ["symbol,date,open,high,low,close,volume" + (",adj_close" if adjusted else "")]
    for symbol, fn in series.items():
        for i, day in enumerate(days):
            close, open_ = fn(i), fn(i - 1) if i else fn(0)
            high, low = max(open_, close) + 0.5, min(open_, close) - 0.5
            if broken_day == i:
                high = low - 1  # impossible bar
            row = f"{symbol},{day},{open_:.4f},{high:.4f},{low:.4f},{close:.4f},1000"
            lines.append(row + (f",{close:.4f}" if adjusted else ""))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def load(
    conn: sqlite3.Connection, tmp_path: Path, calendar: MarketCalendar, name: str, **kwargs: object
) -> int:
    path = write_bars_csv(tmp_path / name, calendar, **kwargs)  # type: ignore[arg-type]
    return import_csv(conn, path, DatasetKind.EQUITY_BARS, "synthetic", calendar, NOW).dataset_id


def request(
    dataset_id: int, period: repo.Period = "in-sample", **kwargs: object
) -> service.RunRequest:
    return service.RunRequest(
        dataset_id=dataset_id,
        symbol=str(kwargs.pop("symbol", "QQQ")),
        strategy_id=str(kwargs.pop("strategy_id", "sma_crossover")),
        period=period,
        params=kwargs.pop("params", {"fast": 5, "slow": 20}),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
def two_symbols(conn: sqlite3.Connection, tmp_path: Path, calendar: MarketCalendar) -> int:
    return load(conn, tmp_path, calendar, "two.csv", series={"QQQ": wave, "SPY": trend})


def test_first_run_locks_the_most_recent_30_percent(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    report = service.run(conn, request(two_symbols), calendar, NOW)
    lock = repo.get_lock(conn, "QQQ")
    assert lock is not None
    sessions = calendar.sessions(date(2023, 1, 3), date(2025, 12, 31))[:300]
    assert lock.oos_start == sessions[210]  # int(300 * 0.7)
    rc = report.reality_check
    assert (rc.window_start, rc.window_end) == (sessions[0], sessions[209])
    assert rc.oos_start == lock.oos_start
    assert rc.sessions == 210
    assert report.benchmark_symbol == "SPY"  # SPY is in the dataset, so it is the benchmark
    assert rc.price_basis == "adjusted"


def test_in_sample_runs_never_receive_out_of_sample_bars(
    conn: sqlite3.Connection,
    calendar: MarketCalendar,
    two_symbols: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[date] = []

    class Spy:
        warmup = 0

        def target_exposure(self, history: History) -> float:
            seen.append(history.current.day)
            return 1.0

    spec = StrategySpec("spy_probe", "probe", "test", (), lambda _p: Spy())
    monkeypatch.setitem(STRATEGIES, "spy_probe", spec)
    service.run(conn, request(two_symbols, strategy_id="spy_probe", params={}), calendar, NOW)
    lock = repo.get_lock(conn, "QQQ")
    assert lock is not None
    assert seen
    assert max(seen) < lock.oos_start


def test_trials_are_counted_per_parameter_combination(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    service.run(conn, request(two_symbols), calendar, NOW)
    again = service.run(conn, request(two_symbols), calendar, NOW)
    assert again.reality_check.parameter_combinations_tried == 1
    assert again.reality_check.in_sample_runs == 2
    third = service.run(conn, request(two_symbols, params={"fast": 10, "slow": 40}), calendar, NOW)
    assert third.reality_check.parameter_combinations_tried == 2
    assert any("2 parameter combinations" in w.text for w in third.reality_check.warnings)
    hold = service.run(
        conn, request(two_symbols, strategy_id="buy_and_hold", params={}), calendar, NOW
    )
    assert hold.reality_check.parameter_combinations_tried == 1
    assert hold.reality_check.parameter_combinations_tried_all_strategies == 3


def test_out_of_sample_evaluations_are_counted_and_warned(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    first = service.run(conn, request(two_symbols, "out-of-sample"), calendar, NOW)
    lock = repo.get_lock(conn, "QQQ")
    assert lock is not None
    assert first.reality_check.window_start == lock.oos_start
    assert first.reality_check.oos_evaluations == 1
    assert not any("evaluated" in w.text for w in first.reality_check.warnings)
    second = service.run(conn, request(two_symbols, "out-of-sample"), calendar, NOW)
    assert second.reality_check.oos_evaluations == 2
    assert any("evaluated 2 times" in w.text for w in second.reality_check.warnings)


def test_locks_and_run_log_are_permanent(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    service.run(conn, request(two_symbols), calendar, NOW)
    with pytest.raises(sqlite3.DatabaseError, match="permanent"):
        conn.execute("UPDATE oos_locks SET oos_start = '2030-01-01'")
    with pytest.raises(sqlite3.DatabaseError, match="permanent"):
        conn.execute("DELETE FROM oos_locks")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM backtest_runs")
    with pytest.raises(service.BacktestRefusedError, match="already has"):
        service.create_lock(conn, two_symbols, "QQQ", NOW)


def test_lock_survives_dataset_deletion_and_reimport(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    from ptl.data import repository as data_repo  # noqa: PLC0415

    first = load(conn, tmp_path, calendar, "a.csv", series={"QQQ": wave})
    service.run(conn, request(first), calendar, NOW)
    data_repo.delete_dataset(conn, first)
    again = load(conn, tmp_path, calendar, "a.csv", series={"QQQ": wave})
    report = service.run(conn, request(again), calendar, NOW)
    assert report.reality_check.in_sample_runs == 2  # history was not reset


def test_explicit_lock_fraction(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    lock = service.create_lock(conn, two_symbols, "QQQ", NOW, fraction=0.2)
    sessions = calendar.sessions(date(2023, 1, 3), date(2025, 12, 31))[:300]
    assert lock.oos_start == sessions[240]
    with pytest.raises(service.BacktestRefusedError, match="between"):
        service.create_lock(conn, two_symbols, "SPY", NOW, fraction=0.9)


def test_error_level_data_blocks_the_run(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "bad.csv", series={"QQQ": wave}, broken_day=50)
    with pytest.raises(service.BacktestRefusedError, match="inconsistent_ohlc"):
        service.run(conn, request(dataset), calendar, NOW)
    assert repo.list_runs(conn) == []  # refused runs are not logged as results


def test_raw_prices_are_flagged(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "raw.csv", series={"QQQ": wave}, adjusted=False)
    report = service.run(conn, request(dataset), calendar, NOW)
    assert report.reality_check.price_basis == "raw"
    assert report.benchmark_symbol == "QQQ"  # no SPY in the dataset: buy-and-hold of itself
    assert any("raw prices" in w.text for w in report.reality_check.warnings)


def test_benchmark_must_cover_the_window(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path, two_symbols: int
) -> None:
    short = load(conn, tmp_path, calendar, "short.csv", series={"IWM": trend}, sessions=100)
    with pytest.raises(service.BacktestRefusedError, match="benchmark IWM covers"):
        service.run(
            conn,
            request(two_symbols, benchmark_dataset_id=short, benchmark_symbol="IWM"),
            calendar,
            NOW,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"strategy_id": "nope"}, "Unknown strategy"),
        ({"params": {"fast": 30, "slow": 20}}, "shorter"),
        ({"symbol": "TSLA"}, "no bars for TSLA"),
        ({"initial_capital": 0.0}, "positive"),
    ],
)
def test_refusals(
    conn: sqlite3.Connection,
    calendar: MarketCalendar,
    two_symbols: int,
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(service.BacktestRefusedError, match=message):
        service.run(conn, request(two_symbols, "in-sample", **kwargs), calendar, NOW)


def test_too_little_data_to_lock(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "tiny.csv", series={"QQQ": wave}, sessions=50)
    with pytest.raises(service.BacktestRefusedError, match="at least 120"):
        service.run(conn, request(dataset), calendar, NOW)


def test_report_compares_with_benchmark_after_costs(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    report = service.run(conn, request(two_symbols), calendar, NOW)
    s, b = report.strategy_metrics, report.benchmark_metrics
    assert b.trade_count == 1  # buy at the start, forced sale at the end
    assert b.total_costs > 0  # same 5 bps slippage on both
    assert s.total_costs > 0
    assert report.excess_annualized_return.value == pytest.approx(
        (s.annualized_return.value or 0) - (b.annualized_return.value or 0)
    )
    assert report.excess_annualized_return.ci95 is not None
    assert len(report.curve) == s.sessions
    rc = report.reality_check
    assert rc.bootstrap.seed == 20251005
    assert rc.bootstrap.resamples == 2000
    assert rc.cost_lines[0].value == "5 bps per side"
    assert any("needs 20 sessions" in w.text for w in rc.warnings)
    assert report.provenance.source.startswith("Backtest on synthetic")


def test_warnings_are_stored_with_codes_and_permanent(
    conn: sqlite3.Connection, calendar: MarketCalendar, two_symbols: int
) -> None:
    report = service.run(conn, request(two_symbols), calendar, NOW)
    stored = repo.warnings_by_run(conn)[report.run_id]
    assert [w.code for w in stored] == [w.code for w in report.reality_check.warnings]
    assert {"warmup_cash", "low_trades"} <= {w.code for w in stored}
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM run_warnings")
    summary = repo.all_runs(conn)[-1].summary
    ci = report.excess_annualized_return.ci95
    assert ci is not None
    assert (summary["excess_ci_low"], summary["excess_ci_high"]) == (ci.low, ci.high)
