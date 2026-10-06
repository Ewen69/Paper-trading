"""Command-line tools: import-csv, datasets, delete-dataset, paper-cycle, kill-switch.

Run from the repo root as `npm run ptl -- <command> ...`.
"""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ptl.config import Settings
from ptl.data import repository
from ptl.data.alpaca_source import AlpacaQuoteSource
from ptl.data.csv_ingest import (
    ALLOWED_DATA_TYPES,
    DEFAULT_DATE_FORMAT,
    ImportRejectedError,
    import_csv,
)
from ptl.data.models import DatasetKind
from ptl.db import migrate, open_db
from ptl.market_calendar import MarketCalendar
from ptl.paper.factory import broker_factory
from ptl.paper.runner import CycleRefusedError, CycleRequest, run_cycle
from ptl.provenance import DataType
from ptl.risk import store as risk_store

_KINDS = {"equity-bars": DatasetKind.EQUITY_BARS, "option-quotes": DatasetKind.OPTION_QUOTES}


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _parse_map(pairs: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        canonical, sep, source = pair.partition("=")
        if not sep or not canonical.strip() or not source.strip():
            raise SystemExit(f"--map expects canonical=source_header, got {pair!r}")
        mapping[canonical.strip().lower()] = source.strip()
    return mapping


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ptl", description="Paper Trading Lab data tools")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-csv", help="import a historical CSV file (see docs/DATA.md)")
    imp.add_argument("path", type=Path)
    imp.add_argument("--kind", required=True, choices=sorted(_KINDS))
    imp.add_argument("--source", required=True, help="where the data came from, e.g. 'Stooq'")
    imp.add_argument(
        "--data-type",
        default=DataType.END_OF_DAY.value,
        choices=sorted(t.value for t in ALLOWED_DATA_TYPES),
    )
    imp.add_argument("--date-format", default=DEFAULT_DATE_FORMAT, help="strptime format")
    imp.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="CANONICAL=HEADER",
        help="rename a column, e.g. --map date=Date (repeatable)",
    )

    sub.add_parser("datasets", help="list imported datasets and their quality findings")

    delete = sub.add_parser("delete-dataset", help="remove an imported dataset and its rows")
    delete.add_argument("dataset_id", type=int)

    cycle = sub.add_parser(
        "paper-cycle", help="run one paper-trading cycle (dry run unless --paper is given)"
    )
    cycle.add_argument("--strategy", required=True)
    cycle.add_argument("--dataset", type=int, required=True, help="equity bars dataset id")
    cycle.add_argument("--symbol", required=True)
    cycle.add_argument("--param", action="append", default=[], metavar="NAME=INT")
    mode = cycle.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="log only; never send (default)")
    mode.add_argument("--paper", action="store_true", help="send to the Alpaca PAPER account")

    kill = sub.add_parser("kill-switch", help="engage or release the risk kill switch")
    kill.add_argument("state", choices=["on", "off"])
    kill.add_argument("--reason", required=True)
    return parser


def _parse_params(pairs: Sequence[str]) -> dict[str, int]:
    params: dict[str, int] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not value.strip().lstrip("-").isdigit():
            raise SystemExit(f"--param expects NAME=INTEGER, got {pair!r}")
        params[name.strip()] = int(value)
    return params


def _paper_cycle(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    settings = Settings()
    now = datetime.now(UTC)
    calendar = MarketCalendar()
    try:
        broker = broker_factory(settings)(not args.paper)
        result = run_cycle(
            conn,
            CycleRequest(args.strategy, _parse_params(args.param), args.dataset, args.symbol),
            settings=settings,
            broker=broker,
            quotes=AlpacaQuoteSource(settings, lambda: datetime.now(UTC)),
            calendar=calendar,
            now=now,
        )
    except CycleRefusedError as exc:
        _out(f"Refused: {exc}")
        return 1
    _out(f"Cycle #{result.cycle_id} ({result.mode}, session {result.session})")
    _out(f"  Signal: {result.explanation}")
    if result.decision is not None:
        for check in result.decision.checks:
            _out(f"  Risk {'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    _out(f"  Outcome: {result.outcome}")
    return 0


def _kill_switch(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    switch = risk_store.set_kill_switch(conn, args.state == "on", args.reason, datetime.now(UTC))
    _out(f"Kill switch {'ENGAGED' if switch.engaged else 'released'}: {switch.reason}")
    return 0


def _import(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    path: Path = args.path.expanduser().resolve()
    if not path.is_file():
        _out(f"error: file not found: {path}")
        return 2
    try:
        result = import_csv(
            conn,
            path,
            _KINDS[args.kind],
            args.source,
            MarketCalendar(),
            datetime.now(UTC),
            data_type=DataType(args.data_type),
            column_map=_parse_map(args.map),
            date_format=args.date_format,
        )
    except ImportRejectedError as exc:
        _out(str(exc))
        return 1
    verb = "Already imported" if result.already_imported else "Imported"
    _out(f"{verb}: dataset #{result.dataset_id}, {result.row_count} rows.")
    for issue in result.issues:
        _out(
            f"  [{issue.severity}] {issue.symbol} {issue.check} x{issue.count} "
            f"({issue.first_date}..{issue.last_date}): {issue.detail}"
        )
    if not result.issues:
        _out("  No quality issues found.")
    return 0


def _list(conn: sqlite3.Connection, _args: argparse.Namespace) -> int:
    records = repository.list_datasets(conn)
    if not records:
        _out("No datasets imported yet.")
    for r in records:
        findings = len(repository.issues_for(conn, r.id))
        _out(
            f"#{r.id} {r.kind} {r.file_name} | source={r.source} type={r.data_type} | "
            f"{r.row_count} rows, {r.symbol_count} symbols, {r.coverage_start}..{r.coverage_end}"
            f" | imported {r.imported_at:%Y-%m-%d %H:%M}Z | {findings} finding(s)"
        )
    return 0


def _delete(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if repository.delete_dataset(conn, args.dataset_id):
        _out(f"Deleted dataset #{args.dataset_id}.")
        return 0
    _out(f"No dataset #{args.dataset_id}.")
    return 1


_COMMANDS = {
    "import-csv": _import,
    "datasets": _list,
    "delete-dataset": _delete,
    "paper-cycle": _paper_cycle,
    "kill-switch": _kill_switch,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with open_db(Settings().database_path) as conn:
        migrate(conn)
        return _COMMANDS[args.command](conn, args)


if __name__ == "__main__":
    raise SystemExit(main())
