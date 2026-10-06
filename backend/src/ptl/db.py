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
    # 2: backtesting rigor. Out-of-sample locks are per symbol (across all datasets) and the run
    # log is append-only, so trial counts can't be reset by deleting or re-importing data.
    """
    CREATE TABLE oos_locks (
        symbol       TEXT PRIMARY KEY,
        oos_start    TEXT NOT NULL,
        oos_fraction REAL NOT NULL,
        locked_at    TEXT NOT NULL,
        basis        TEXT NOT NULL
    );
    CREATE TRIGGER oos_locks_no_update BEFORE UPDATE ON oos_locks
    BEGIN SELECT RAISE(ABORT, 'out-of-sample locks are permanent'); END;
    CREATE TRIGGER oos_locks_no_delete BEFORE DELETE ON oos_locks
    BEGIN SELECT RAISE(ABORT, 'out-of-sample locks are permanent'); END;

    CREATE TABLE backtest_runs (
        id               INTEGER PRIMARY KEY,
        created_at       TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        period           TEXT NOT NULL CHECK (period IN ('in-sample', 'out-of-sample')),
        strategy         TEXT NOT NULL,
        params           TEXT NOT NULL,
        costs            TEXT NOT NULL,
        dataset_sha256   TEXT NOT NULL,
        window_start     TEXT NOT NULL,
        window_end       TEXT NOT NULL,
        benchmark_symbol TEXT NOT NULL,
        summary          TEXT NOT NULL
    );
    CREATE INDEX backtest_runs_symbol ON backtest_runs (symbol, period);
    CREATE TRIGGER backtest_runs_no_update BEFORE UPDATE ON backtest_runs
    BEGIN SELECT RAISE(ABORT, 'the backtest run log is append-only'); END;
    CREATE TRIGGER backtest_runs_no_delete BEFORE DELETE ON backtest_runs
    BEGIN SELECT RAISE(ABORT, 'the backtest run log is append-only'); END;
    """,
    # 3: warnings each backtest produced, with stable codes, for the Auditor. Append-only.
    """
    CREATE TABLE run_warnings (
        run_id   INTEGER NOT NULL REFERENCES backtest_runs (id),
        position INTEGER NOT NULL,
        code     TEXT NOT NULL,
        text     TEXT NOT NULL,
        PRIMARY KEY (run_id, position)
    ) WITHOUT ROWID;
    CREATE TRIGGER run_warnings_no_update BEFORE UPDATE ON run_warnings
    BEGIN SELECT RAISE(ABORT, 'run warnings are append-only'); END;
    CREATE TRIGGER run_warnings_no_delete BEFORE DELETE ON run_warnings
    BEGIN SELECT RAISE(ABORT, 'run warnings are append-only'); END;
    """,
    # 4: per-session chain lookups for the options engine.
    """
    CREATE INDEX option_quotes_by_day ON option_quotes (dataset_id, underlying, quote_date);
    """,
    # 5: risk engine state, paper trading log, and the agent subsystem.
    """
    CREATE TABLE risk_state (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        engaged    INTEGER NOT NULL,
        reason     TEXT NOT NULL,
        changed_at TEXT NOT NULL
    );
    CREATE TABLE risk_decisions (
        id         INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        source     TEXT NOT NULL,
        symbol     TEXT NOT NULL,
        order_text TEXT NOT NULL,
        approved   INTEGER NOT NULL,
        checks     TEXT NOT NULL,
        tripped    INTEGER NOT NULL
    );

    CREATE TABLE paper_runners (
        id         INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        strategy   TEXT NOT NULL,
        params     TEXT NOT NULL,
        dataset_id INTEGER NOT NULL,
        symbol     TEXT NOT NULL,
        dry_run    INTEGER NOT NULL,
        active     INTEGER NOT NULL
    );
    CREATE TABLE paper_cycles (
        id            INTEGER PRIMARY KEY,
        runner_id     INTEGER,
        created_at    TEXT NOT NULL,
        session       TEXT NOT NULL,
        mode          TEXT NOT NULL CHECK (mode IN ('dry_run', 'paper')),
        strategy      TEXT NOT NULL,
        params        TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        explanation   TEXT NOT NULL,
        target        REAL,
        price         REAL,
        price_source  TEXT NOT NULL,
        current_qty   REAL,
        desired_qty   REAL,
        outcome       TEXT NOT NULL
    );
    CREATE TABLE paper_orders (
        id               INTEGER PRIMARY KEY,
        cycle_id         INTEGER NOT NULL REFERENCES paper_cycles (id),
        created_at       TEXT NOT NULL,
        mode             TEXT NOT NULL CHECK (mode IN ('dry_run', 'paper')),
        symbol           TEXT NOT NULL,
        side             TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
        qty              REAL NOT NULL,
        reason           TEXT NOT NULL,
        risk_decision_id INTEGER NOT NULL REFERENCES risk_decisions (id),
        broker_order_id  TEXT,
        status           TEXT NOT NULL
    );
    CREATE TABLE paper_order_events (
        id          INTEGER PRIMARY KEY,
        order_id    INTEGER NOT NULL REFERENCES paper_orders (id),
        at          TEXT NOT NULL,
        status      TEXT NOT NULL,
        filled_qty  REAL,
        fill_price  REAL,
        detail      TEXT NOT NULL
    );

    CREATE TABLE agent_jobs (
        id          INTEGER PRIMARY KEY,
        agent_id    TEXT NOT NULL,
        kind        TEXT NOT NULL,
        spec        TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
        created_at  TEXT NOT NULL,
        started_at  TEXT,
        finished_at TEXT,
        summary     TEXT
    );
    CREATE TABLE agent_findings (
        id                INTEGER PRIMARY KEY,
        job_id            INTEGER NOT NULL REFERENCES agent_jobs (id),
        agent_id          TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        symbol            TEXT NOT NULL,
        strategy          TEXT NOT NULL,
        params            TEXT NOT NULL,
        period            TEXT NOT NULL,
        run_id            INTEGER,
        trades            INTEGER,
        win_rate          REAL,
        sharpe            REAL,
        total_return      REAL,
        excess_annualized REAL,
        note              TEXT NOT NULL
    );

    CREATE TRIGGER risk_decisions_no_update BEFORE UPDATE ON risk_decisions
    BEGIN SELECT RAISE(ABORT, 'the risk decision log is append-only'); END;
    CREATE TRIGGER risk_decisions_no_delete BEFORE DELETE ON risk_decisions
    BEGIN SELECT RAISE(ABORT, 'the risk decision log is append-only'); END;
    CREATE TRIGGER paper_cycles_no_update BEFORE UPDATE ON paper_cycles
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER paper_cycles_no_delete BEFORE DELETE ON paper_cycles
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER paper_orders_no_update BEFORE UPDATE ON paper_orders
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER paper_orders_no_delete BEFORE DELETE ON paper_orders
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER paper_order_events_no_update BEFORE UPDATE ON paper_order_events
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER paper_order_events_no_delete BEFORE DELETE ON paper_order_events
    BEGIN SELECT RAISE(ABORT, 'the paper trading log is append-only'); END;
    CREATE TRIGGER agent_findings_no_update BEFORE UPDATE ON agent_findings
    BEGIN SELECT RAISE(ABORT, 'agent findings are append-only'); END;
    CREATE TRIGGER agent_findings_no_delete BEFORE DELETE ON agent_findings
    BEGIN SELECT RAISE(ABORT, 'agent findings are append-only'); END;
    """,
    # 6: net worth, entered by hand or from CSV. Amounts are non-negative cents; the account's
    # kind decides the sign. One balance per account per date (a re-entry replaces it).
    """
    CREATE TABLE nw_accounts (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind       TEXT NOT NULL CHECK (kind IN ('asset', 'liability')),
        category   TEXT NOT NULL,
        note       TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE nw_balances (
        id           INTEGER PRIMARY KEY,
        account_id   INTEGER NOT NULL REFERENCES nw_accounts (id) ON DELETE CASCADE,
        as_of        TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
        source       TEXT NOT NULL,
        entered_at   TEXT NOT NULL,
        UNIQUE (account_id, as_of)
    );
    CREATE INDEX nw_balances_by_date ON nw_balances (as_of);
    """,
    # 7: manual portfolio holdings. Paper positions are read live from the Alpaca paper account
    # and never stored here. Options carry their contract terms; quantity < 0 means short.
    """
    CREATE TABLE pf_holdings (
        id                 INTEGER PRIMARY KEY,
        symbol             TEXT NOT NULL,
        asset_class        TEXT NOT NULL CHECK (asset_class IN ('stock', 'etf', 'fund',
                               'bond', 'cash', 'option', 'crypto', 'other')),
        quantity           REAL NOT NULL CHECK (quantity <> 0),
        account            TEXT NOT NULL DEFAULT '',
        option_type        TEXT CHECK (option_type IN ('call', 'put')),
        strike             REAL,
        expiration         TEXT,
        manual_price       REAL CHECK (manual_price IS NULL OR manual_price >= 0),
        manual_price_as_of TEXT,
        note               TEXT NOT NULL DEFAULT '',
        created_at         TEXT NOT NULL,
        CHECK ((asset_class = 'option') = (option_type IS NOT NULL AND strike IS NOT NULL
                                           AND expiration IS NOT NULL)),
        CHECK ((manual_price IS NULL) = (manual_price_as_of IS NULL)),
        CHECK (asset_class = 'option' OR quantity > 0)
    );
    """,
    # 8: the learning optimizer and background daemons. Trials, Active Best history and daemon
    # log lines are append-only; heartbeats are a small upserted status table.
    """
    CREATE TABLE agent_learning_log (
        id                 INTEGER PRIMARY KEY,
        created_at         TEXT NOT NULL,
        search_id          TEXT NOT NULL,
        generation         INTEGER NOT NULL,
        asset              TEXT NOT NULL CHECK (asset IN ('equity', 'options')),
        strategy           TEXT NOT NULL,
        symbol             TEXT NOT NULL,
        dataset_id         INTEGER NOT NULL,
        options_dataset_id INTEGER,
        params             TEXT NOT NULL,
        period             TEXT NOT NULL CHECK (period IN ('in-sample', 'out-of-sample')),
        run_id             INTEGER,
        reused             INTEGER NOT NULL,
        trades             INTEGER,
        win_rate           REAL,
        sharpe             REAL,
        max_drawdown       REAL,
        total_return       REAL,
        excess_annualized  REAL,
        fitness            REAL,
        verdict            TEXT NOT NULL,
        note               TEXT NOT NULL
    );
    CREATE INDEX agent_learning_log_target ON agent_learning_log (strategy, symbol, period);

    CREATE TABLE optimizer_active_best (
        id                 INTEGER PRIMARY KEY,
        created_at         TEXT NOT NULL,
        search_id          TEXT NOT NULL,
        asset              TEXT NOT NULL CHECK (asset IN ('equity', 'options')),
        strategy           TEXT NOT NULL,
        symbol             TEXT NOT NULL,
        dataset_id         INTEGER NOT NULL,
        options_dataset_id INTEGER,
        params             TEXT NOT NULL,
        in_sample_fitness  REAL NOT NULL,
        in_sample_log_id   INTEGER NOT NULL REFERENCES agent_learning_log (id),
        oos_log_id         INTEGER REFERENCES agent_learning_log (id),
        validated          INTEGER NOT NULL,
        reason             TEXT NOT NULL,
        curve              TEXT NOT NULL
    );

    CREATE TABLE daemon_log (
        id         INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        daemon     TEXT NOT NULL,
        level      TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
        message    TEXT NOT NULL
    );

    CREATE TABLE daemon_heartbeats (
        daemon     TEXT PRIMARY KEY,
        pid        INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        beat_at    TEXT NOT NULL,
        status     TEXT NOT NULL,
        detail     TEXT NOT NULL
    );

    CREATE TRIGGER agent_learning_log_no_update BEFORE UPDATE ON agent_learning_log
    BEGIN SELECT RAISE(ABORT, 'the learning log is append-only'); END;
    CREATE TRIGGER agent_learning_log_no_delete BEFORE DELETE ON agent_learning_log
    BEGIN SELECT RAISE(ABORT, 'the learning log is append-only'); END;
    CREATE TRIGGER optimizer_active_best_no_update BEFORE UPDATE ON optimizer_active_best
    BEGIN SELECT RAISE(ABORT, 'Active Best history is append-only'); END;
    CREATE TRIGGER optimizer_active_best_no_delete BEFORE DELETE ON optimizer_active_best
    BEGIN SELECT RAISE(ABORT, 'Active Best history is append-only'); END;
    CREATE TRIGGER daemon_log_no_update BEFORE UPDATE ON daemon_log
    BEGIN SELECT RAISE(ABORT, 'the daemon log is append-only'); END;
    CREATE TRIGGER daemon_log_no_delete BEFORE DELETE ON daemon_log
    BEGIN SELECT RAISE(ABORT, 'the daemon log is append-only'); END;
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
