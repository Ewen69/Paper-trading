"""SQLite access and forward-only schema migrations. All data stays in a local file."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: tuple[str, ...] = (
    # 1: historical datasets imported from CSV, plus their aggregated quality findings.
    """
    CREATE TABLE datasets (
        id             INTEGER PRIMARY KEY,
        kind           TEXT NOT NULL CHECK (kind IN ('equity_bars', 'option_quotes')),
        source         TEXT NOT NULL,
        data_type      TEXT NOT NULL,
        file_name      TEXT NOT NULL,
        sha256         TEXT NOT NULL UNIQUE,
        imported_at    TEXT NOT NULL,
        row_count      INTEGER NOT NULL,
        symbol_count   INTEGER NOT NULL,
        coverage_start TEXT NOT NULL,
        coverage_end   TEXT NOT NULL
    );

    CREATE TABLE equity_bars (
        dataset_id   INTEGER NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
        symbol       TEXT NOT NULL,
        session_date TEXT NOT NULL,
        open         REAL NOT NULL,
        high         REAL NOT NULL,
        low          REAL NOT NULL,
        close        REAL NOT NULL,
        volume       INTEGER NOT NULL,
        adj_close    REAL,
        PRIMARY KEY (dataset_id, symbol, session_date)
    ) WITHOUT ROWID;

    CREATE TABLE option_quotes (
        dataset_id         INTEGER NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
        underlying         TEXT NOT NULL,
        root               TEXT NOT NULL DEFAULT '',
        quote_date         TEXT NOT NULL,
        expiration         TEXT NOT NULL,
        strike             REAL NOT NULL,
        option_type        TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
        bid                REAL NOT NULL,
        ask                REAL NOT NULL,
        exercise_style     TEXT CHECK (exercise_style IN ('american', 'european')),
        bid_size           INTEGER,
        ask_size           INTEGER,
        last               REAL,
        volume             INTEGER,
        open_interest      INTEGER,
        underlying_price   REAL,
        implied_volatility REAL,
        delta              REAL,
        gamma              REAL,
        theta              REAL,
        vega               REAL,
        PRIMARY KEY (dataset_id, underlying, root, quote_date, expiration, strike, option_type)
    ) WITHOUT ROWID;

    CREATE TABLE quality_issues (
        id         INTEGER PRIMARY KEY,
        dataset_id INTEGER NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
        check_name TEXT NOT NULL,
        severity   TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
        symbol     TEXT NOT NULL,
        count      INTEGER NOT NULL,
        first_date TEXT NOT NULL,
        last_date  TEXT NOT NULL,
        detail     TEXT NOT NULL
    );
    CREATE INDEX quality_issues_dataset ON quality_issues (dataset_id);
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; return the resulting schema version."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    return len(MIGRATIONS)


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
