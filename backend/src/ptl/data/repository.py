"""SQL for datasets and their quality findings."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from ptl.data.models import DatasetKind, QualityIssue, Severity
from ptl.provenance import DataType


@dataclass(frozen=True, slots=True)
class NewDataset:
    kind: DatasetKind
    row_count: int
    symbol_count: int
    coverage_start: date
    coverage_end: date

    @classmethod
    def describe(cls, kind: DatasetKind, keys: Sequence[tuple[str, date]]) -> "NewDataset":
        days = [d for _, d in keys]
        return cls(kind, len(keys), len({s for s, _ in keys}), min(days), max(days))


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    id: int
    kind: DatasetKind
    source: str
    data_type: DataType
    file_name: str
    sha256: str
    imported_at: datetime
    row_count: int
    symbol_count: int
    coverage_start: date
    coverage_end: date


def _record(row: sqlite3.Row) -> DatasetRecord:
    return DatasetRecord(
        id=row["id"],
        kind=DatasetKind(row["kind"]),
        source=row["source"],
        data_type=DataType(row["data_type"]),
        file_name=row["file_name"],
        sha256=row["sha256"],
        imported_at=datetime.fromisoformat(row["imported_at"]),
        row_count=row["row_count"],
        symbol_count=row["symbol_count"],
        coverage_start=date.fromisoformat(row["coverage_start"]),
        coverage_end=date.fromisoformat(row["coverage_end"]),
    )


def insert_dataset(  # noqa: PLR0913
    conn: sqlite3.Connection,
    meta: NewDataset,
    *,
    source: str,
    data_type: DataType,
    file_name: str,
    sha256: str,
    imported_at: datetime,
) -> int:
    cursor = conn.execute(
        "INSERT INTO datasets (kind, source, data_type, file_name, sha256, imported_at, "
        "row_count, symbol_count, coverage_start, coverage_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            meta.kind.value,
            source,
            data_type.value,
            file_name,
            sha256,
            imported_at.isoformat(),
            meta.row_count,
            meta.symbol_count,
            meta.coverage_start.isoformat(),
            meta.coverage_end.isoformat(),
        ),
    )
    if cursor.lastrowid is None:  # pragma: no cover - sqlite always sets it for INSERT
        raise RuntimeError("dataset insert returned no id")
    return cursor.lastrowid


def insert_issues(
    conn: sqlite3.Connection, dataset_id: int, issues: Sequence[QualityIssue]
) -> None:
    conn.executemany(
        "INSERT INTO quality_issues (dataset_id, check_name, severity, symbol, count, "
        "first_date, last_date, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                dataset_id,
                i.check,
                i.severity.value,
                i.symbol,
                i.count,
                i.first_date.isoformat(),
                i.last_date.isoformat(),
                i.detail,
            )
            for i in issues
        ),
    )


def find_dataset_by_hash(conn: sqlite3.Connection, sha256: str) -> DatasetRecord | None:
    row = conn.execute("SELECT * FROM datasets WHERE sha256 = ?", (sha256,)).fetchone()
    return None if row is None else _record(row)


def list_datasets(conn: sqlite3.Connection) -> list[DatasetRecord]:
    return [_record(r) for r in conn.execute("SELECT * FROM datasets ORDER BY id")]


def issues_for(conn: sqlite3.Connection, dataset_id: int) -> list[QualityIssue]:
    rows = conn.execute(
        "SELECT * FROM quality_issues WHERE dataset_id = ? ORDER BY id", (dataset_id,)
    )
    return [
        QualityIssue(
            check=r["check_name"],
            severity=Severity(r["severity"]),
            symbol=r["symbol"],
            count=r["count"],
            first_date=date.fromisoformat(r["first_date"]),
            last_date=date.fromisoformat(r["last_date"]),
            detail=r["detail"],
        )
        for r in rows
    ]


def delete_dataset(conn: sqlite3.Connection, dataset_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,)).rowcount > 0
