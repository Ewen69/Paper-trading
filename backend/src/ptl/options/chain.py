"""Read option chains from SQLite, one session at a time (so strategies only see today)."""

import sqlite3
from dataclasses import dataclass
from datetime import date

from ptl.options.models import Contract, ContractKey, Quote


class MissingExerciseStyleError(ValueError):
    """Quotes without exercise_style can't be settled; the engine won't assume a style."""


@dataclass(frozen=True, slots=True)
class OptionUniverseEntry:
    dataset_id: int
    underlying: str
    source: str
    file_name: str
    first_date: date
    last_date: date
    quote_days: int
    rows: int
    rows_missing_style: int


def option_universe(conn: sqlite3.Connection) -> list[OptionUniverseEntry]:
    rows = conn.execute(
        "SELECT q.dataset_id, q.underlying, d.source, d.file_name, MIN(q.quote_date) AS first, "
        "MAX(q.quote_date) AS last, COUNT(DISTINCT q.quote_date) AS days, COUNT(*) AS n, "
        "SUM(q.exercise_style IS NULL) AS no_style "
        "FROM option_quotes q JOIN datasets d ON d.id = q.dataset_id "
        "GROUP BY q.dataset_id, q.underlying ORDER BY q.underlying, q.dataset_id"
    )
    return [
        OptionUniverseEntry(
            dataset_id=r["dataset_id"],
            underlying=r["underlying"],
            source=r["source"],
            file_name=r["file_name"],
            first_date=date.fromisoformat(r["first"]),
            last_date=date.fromisoformat(r["last"]),
            quote_days=r["days"],
            rows=r["n"],
            rows_missing_style=r["no_style"],
        )
        for r in rows
    ]


def count_missing_style(
    conn: sqlite3.Connection, dataset_id: int, underlying: str, start: date, end: date
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM option_quotes WHERE dataset_id = ? AND underlying = ? "
        "AND quote_date BETWEEN ? AND ? AND exercise_style IS NULL",
        (dataset_id, underlying, start.isoformat(), end.isoformat()),
    ).fetchone()
    return int(row[0])


def load_chain(
    conn: sqlite3.Connection, dataset_id: int, underlying: str, day: date
) -> dict[ContractKey, Quote]:
    """All quotes for `underlying` on `day`. Raises if a quote lacks exercise_style."""
    chain: dict[ContractKey, Quote] = {}
    rows = conn.execute(
        "SELECT underlying, root, expiration, strike, option_type, exercise_style, bid, ask "
        "FROM option_quotes WHERE dataset_id = ? AND underlying = ? AND quote_date = ?",
        (dataset_id, underlying, day.isoformat()),
    )
    for r in rows:
        if r["exercise_style"] is None:
            raise MissingExerciseStyleError(
                f"{underlying} quotes on {day} have no exercise_style; add the column "
                "(american/european) and re-import. The engine won't assume one."
            )
        contract = Contract(
            underlying=r["underlying"],
            root=r["root"],
            expiration=date.fromisoformat(r["expiration"]),
            strike=r["strike"],
            option_type=r["option_type"],
            style=r["exercise_style"],
        )
        chain[contract.key] = Quote(contract, day, r["bid"], r["ask"])
    return chain
