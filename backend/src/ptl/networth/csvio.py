"""CSV import and export for net-worth balances. Imports are all-or-nothing.

Format (header required, one balance per row):

    account,kind,category,as_of,amount
    Checking,asset,cash,2026-09-30,4200.15
    Mortgage,liability,mortgage,2026-09-30,212000

Amounts are positive; the kind decides the sign. Unknown accounts are created; a known account
must match its stored kind and category. A row for an account and date that already exists
replaces it (reported as updated). Exports use the same format, so they round-trip.
"""

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from ptl.networth import store

HEADER = ("account", "kind", "category", "as_of", "amount")
MAX_ROWS = 50_000
MAX_ERRORS_SHOWN = 20


class CsvRejectedError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        shown = errors[:MAX_ERRORS_SHOWN]
        more = len(errors) - len(shown)
        tail = f"\n... and {more} more" if more else ""
        super().__init__("Import rejected; nothing was saved.\n" + "\n".join(shown) + tail)
        self.errors = errors


@dataclass(frozen=True, slots=True)
class ImportResult:
    rows: int
    accounts_created: int
    inserted: int
    updated: int
    source: str


@dataclass(frozen=True, slots=True)
class _Row:
    line: int
    account: str
    kind: str
    category: str
    as_of: date
    cents: int


def _parse(text: str, today: date) -> tuple[list[_Row], list[str]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if header is None or tuple(h.strip().lower() for h in header) != HEADER:
        return [], [f"Line 1: header must be exactly {','.join(HEADER)}."]
    rows: list[_Row] = []
    errors: list[str] = []
    seen: set[tuple[str, date]] = set()
    for line, raw in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw):
            continue
        if len(raw) != len(HEADER):
            errors.append(f"Line {line}: expected {len(HEADER)} columns, got {len(raw)}.")
            continue
        name, kind, category, as_of_text, amount_text = (c.strip() for c in raw)
        try:
            name, kind, category = store.validate_account(name, kind.lower(), category.lower())
            as_of = date.fromisoformat(as_of_text)
            store.check_date(as_of, today)
            cents = store.to_cents(float(amount_text))
        except store.NetWorthError as exc:
            errors.append(f"Line {line}: {exc}")
            continue
        except ValueError:
            errors.append(
                f"Line {line}: date must be YYYY-MM-DD and amount a plain number "
                f"(got {as_of_text!r}, {amount_text!r})."
            )
            continue
        key = (name.lower(), as_of)
        if key in seen:
            errors.append(f"Line {line}: {name} already has a balance for {as_of} in this file.")
            continue
        seen.add(key)
        rows.append(_Row(line, name, kind, category, as_of, cents))
        if len(rows) > MAX_ROWS:
            return [], [f"More than {MAX_ROWS} rows; split the file."]
    if not rows and not errors:
        errors.append("The file has no data rows.")
    return rows, errors


def import_csv(conn: sqlite3.Connection, text: str, file_name: str, now: datetime) -> ImportResult:
    rows, errors = _parse(text, now.date())
    known = {a.name.lower(): a for a in store.accounts(conn)}
    planned: dict[str, tuple[str, str]] = {}
    for r in rows:
        existing = known.get(r.account.lower())
        expected = (
            (existing.kind, existing.category) if existing else planned.get(r.account.lower())
        )
        if expected and expected != (r.kind, r.category):
            errors.append(
                f"Line {r.line}: {r.account} is a {expected[0]} ({expected[1]}), "
                f"not a {r.kind} ({r.category})."
            )
        planned.setdefault(r.account.lower(), (r.kind, r.category))
    if errors:
        raise CsvRejectedError(errors)

    source = f"CSV import: {file_name.strip() or 'unnamed file'}"
    created = inserted = updated = 0
    with conn:
        ids = {name: a.id for name, a in known.items()}
        for r in rows:
            key = r.account.lower()
            if key not in ids:
                cur = conn.execute(
                    "INSERT INTO nw_accounts (name, kind, category, note, created_at) "
                    "VALUES (?, ?, ?, '', ?)",
                    (r.account, r.kind, r.category, now.isoformat()),
                )
                ids[key] = int(cur.lastrowid or 0)
                created += 1
            _, replaced = store.upsert_balance(
                conn,
                account_id=ids[key],
                as_of=r.as_of,
                amount_cents=r.cents,
                source=source,
                now=now,
            )
            updated += replaced
            inserted += not replaced
    return ImportResult(len(rows), created, inserted, updated, source)


def export_csv(conn: sqlite3.Connection) -> str:
    accounts = {a.id: a for a in store.accounts(conn)}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    rows = sorted(
        store.balances(conn), key=lambda b: (accounts[b.account_id].name.lower(), b.as_of)
    )
    for b in rows:
        a = accounts[b.account_id]
        writer.writerow(
            [a.name, a.kind, a.category, b.as_of.isoformat(), f"{b.amount_cents / 100:.2f}"]
        )
    return out.getvalue()
