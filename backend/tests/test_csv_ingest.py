import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from ptl.data import repository
from ptl.data.csv_ingest import DEFAULT_DATE_FORMAT, ImportRejectedError, import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType
from tests.conftest import FIXTURES, WEEKEND_NOW, CsvWriter


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def _import_bars(
    conn: sqlite3.Connection,
    path: Path,
    cal: MarketCalendar,
    column_map: dict[str, str] | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
) -> repository.DatasetRecord:
    result = import_csv(
        conn,
        path,
        DatasetKind.EQUITY_BARS,
        "unit test",
        cal,
        WEEKEND_NOW,
        column_map=column_map,
        date_format=date_format,
    )
    [record] = [r for r in repository.list_datasets(conn) if r.id == result.dataset_id]
    return record


def test_imports_equity_bars_with_column_mapping(
    conn: sqlite3.Connection, calendar: MarketCalendar
) -> None:
    record = _import_bars(
        conn,
        FIXTURES / "synthetic_equity_bars.csv",
        calendar,
        column_map={"adj_close": "Adj Close"},
    )
    assert record.kind is DatasetKind.EQUITY_BARS
    assert record.source == "unit test"
    assert record.data_type is DataType.END_OF_DAY
    assert (record.row_count, record.symbol_count) == (6, 1)
    assert str(record.coverage_start) == "2025-01-02"
    assert str(record.coverage_end) == "2025-01-10"
    assert repository.issues_for(conn, record.id) == []  # Jan 9 closure is not a gap
    row = conn.execute(
        "SELECT * FROM equity_bars WHERE session_date = '2025-01-07' AND symbol = 'SPY'"
    ).fetchone()
    assert (row["open"], row["high"], row["low"], row["close"]) == (597.42, 597.75, 586.78, 588.63)
    assert row["volume"] == 60393069
    assert row["adj_close"] == 588.63


def test_reimport_of_same_file_is_idempotent(
    conn: sqlite3.Connection, calendar: MarketCalendar
) -> None:
    path = FIXTURES / "synthetic_equity_bars.csv"
    first = import_csv(conn, path, DatasetKind.EQUITY_BARS, "s", calendar, WEEKEND_NOW)
    second = import_csv(conn, path, DatasetKind.EQUITY_BARS, "s", calendar, WEEKEND_NOW)
    assert second.already_imported
    assert second.dataset_id == first.dataset_id
    assert conn.execute("SELECT COUNT(*) FROM equity_bars").fetchone()[0] == 6


def test_imports_option_quotes(conn: sqlite3.Connection, calendar: MarketCalendar) -> None:
    result = import_csv(
        conn,
        FIXTURES / "synthetic_option_quotes.csv",
        DatasetKind.OPTION_QUOTES,
        "unit test",
        calendar,
        WEEKEND_NOW,
    )
    assert result.row_count == 4
    assert result.issues == []
    rows = conn.execute(
        "SELECT option_type, bid, ask, exercise_style, root, delta FROM option_quotes "
        "WHERE quote_date = '2025-01-03' ORDER BY option_type"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("call", 11.50, 11.62, "american", "", None),
        ("put", 4.95, 5.05, "american", "", None),
    ]


def test_quality_issues_are_stored_not_rejected(
    conn: sqlite3.Connection, calendar: MarketCalendar, write_csv: CsvWriter
) -> None:
    path = write_csv(
        "crossed.csv",
        """
        underlying,quote_date,expiration,strike,option_type,bid,ask
        SPY,2025-01-02,2025-01-17,585,call,9.00,8.00
        """.replace("        ", ""),
    )
    result = import_csv(conn, path, DatasetKind.OPTION_QUOTES, "s", calendar, WEEKEND_NOW)
    assert not result.already_imported
    assert [i.check for i in repository.issues_for(conn, result.dataset_id)] == ["crossed_market"]


def test_custom_date_format(
    conn: sqlite3.Connection, calendar: MarketCalendar, write_csv: CsvWriter
) -> None:
    path = write_csv(
        "us_dates.csv",
        "symbol,date,open,high,low,close,volume\nQQQ,01/02/2025,1,2,0.5,1.5,100",
    )
    record = _import_bars(conn, path, calendar, date_format="%m/%d/%Y")
    assert str(record.coverage_start) == "2025-01-02"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("symbol,date,open,high,low,close\nSPY,2025-01-02,1,2,0.5,1.5", "missing required column"),
        ("symbol,date,open,high,low,close,volume\nSPY,2025-01-02,1,x,0.5,1.5,1", "high='x'"),
        (
            "symbol,date,open,high,low,close,volume\nSPY,2025-13-02,1,2,0.5,1.5,1",
            "not a valid date",
        ),
        ("symbol,date,open,high,low,close,volume\nSPY,2025-01-02,1,2,0.5,,1", "close is empty"),
        ("symbol,date,open,high,low,close,volume\nSPY,2025-01-02,1,2,0.5,1.5,1.5", "whole number"),
        ("symbol,date,open,high,low,close,volume\nSPY,2025-01-02,1,2,0.5,nan,1", "close is empty"),
        ("symbol,date,open,high,low,close,volume\nSPY,2025-01-02,1,2,0.5,inf,1", "not finite"),
        (
            "symbol,date,open,high,low,close,volume\n"
            "SPY,2025-01-02,1,2,0.5,1.5,1\nspy,2025-01-02,1,2,0.5,1.5,1",
            "line 3: duplicate of line 2",
        ),
        ("symbol,date,open,high,low,close,volume", "no data rows"),
        ("", "file is empty"),
    ],
)
def test_bad_files_are_rejected_without_partial_writes(
    conn: sqlite3.Connection,
    calendar: MarketCalendar,
    write_csv: CsvWriter,
    body: str,
    message: str,
) -> None:
    path = write_csv("bad.csv", body)
    with pytest.raises(ImportRejectedError, match=message):
        import_csv(conn, path, DatasetKind.EQUITY_BARS, "s", calendar, WEEKEND_NOW)
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM equity_bars").fetchone()[0] == 0


def test_option_type_and_style_validation(
    conn: sqlite3.Connection, calendar: MarketCalendar, write_csv: CsvWriter
) -> None:
    header = "underlying,quote_date,expiration,strike,option_type,bid,ask,exercise_style"
    path = write_csv("bad_opt.csv", f"{header}\nSPY,2025-01-02,2025-01-17,585,X,1,2,bermudan")
    with pytest.raises(ImportRejectedError, match="call/put"):
        import_csv(conn, path, DatasetKind.OPTION_QUOTES, "s", calendar, WEEKEND_NOW)


def test_requires_source_and_historical_data_type(
    conn: sqlite3.Connection, calendar: MarketCalendar
) -> None:
    path = FIXTURES / "synthetic_equity_bars.csv"
    with pytest.raises(ImportRejectedError, match="source is required"):
        import_csv(conn, path, DatasetKind.EQUITY_BARS, "  ", calendar, WEEKEND_NOW)
    with pytest.raises(ImportRejectedError, match="not valid for CSV"):
        import_csv(
            conn,
            path,
            DatasetKind.EQUITY_BARS,
            "s",
            calendar,
            WEEKEND_NOW,
            data_type=DataType.REAL_TIME,
        )


def test_unknown_mapped_column_is_rejected(
    conn: sqlite3.Connection, calendar: MarketCalendar
) -> None:
    with pytest.raises(ImportRejectedError, match="unknown column"):
        import_csv(
            conn,
            FIXTURES / "synthetic_equity_bars.csv",
            DatasetKind.EQUITY_BARS,
            "s",
            calendar,
            WEEKEND_NOW,
            column_map={"price": "Close"},
        )


def test_delete_dataset_cascades(conn: sqlite3.Connection, calendar: MarketCalendar) -> None:
    record = _import_bars(conn, FIXTURES / "synthetic_equity_bars.csv", calendar)
    assert repository.delete_dataset(conn, record.id)
    assert conn.execute("SELECT COUNT(*) FROM equity_bars").fetchone()[0] == 0
    assert not repository.delete_dataset(conn, record.id)
