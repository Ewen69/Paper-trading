"""SQL for net-worth accounts and dated balances. Everything stays in the local SQLite file."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Kind = Literal["asset", "liability"]

CATEGORIES: dict[Kind, tuple[str, ...]] = {
    "asset": ("cash", "brokerage", "retirement", "real_estate", "vehicle", "crypto", "other"),
    "liability": ("mortgage", "student_loan", "auto_loan", "credit_card", "other_loan", "other"),
}
MANUAL_SOURCE = "manual entry"
MAX_NAME_LENGTH = 80
MAX_AMOUNT = 1e13


class NetWorthError(ValueError):
    """A request that would store invalid or ambiguous net-worth data."""


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    name: str
    kind: Kind
    category: str
    note: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Balance:
    id: int
    account_id: int
    as_of: date
    amount_cents: int
    source: str
    entered_at: datetime


def _account(r: sqlite3.Row) -> Account:
    return Account(
        id=r["id"],
        name=r["name"],
        kind=r["kind"],
        category=r["category"],
        note=r["note"],
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def _balance(r: sqlite3.Row) -> Balance:
    return Balance(
        id=r["id"],
        account_id=r["account_id"],
        as_of=date.fromisoformat(r["as_of"]),
        amount_cents=r["amount_cents"],
        source=r["source"],
        entered_at=datetime.fromisoformat(r["entered_at"]),
    )


def validate_account(name: str, kind: str, category: str) -> tuple[str, Kind, str]:
    clean = name.strip()
    if not clean or len(clean) > MAX_NAME_LENGTH:
        raise NetWorthError("Account name must be 1 to 80 characters.")
    if kind not in CATEGORIES:
        raise NetWorthError(f"Kind must be 'asset' or 'liability', not {kind!r}.")
    checked: Kind = "asset" if kind == "asset" else "liability"
    if category not in CATEGORIES[checked]:
        allowed = ", ".join(CATEGORIES[checked])
        raise NetWorthError(
            f"Category {category!r} isn't valid for a {kind}. Use one of: {allowed}."
        )
    return clean, checked, category


def create_account(  # noqa: PLR0913
    conn: sqlite3.Connection, *, name: str, kind: str, category: str, note: str, now: datetime
) -> Account:
    clean, checked, category = validate_account(name, kind, category)
    if account_by_name(conn, clean) is not None:
        raise NetWorthError(f"An account named {clean!r} already exists.")
    with conn:
        cur = conn.execute(
            "INSERT INTO nw_accounts (name, kind, category, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (clean, checked, category, note.strip()[:200], now.isoformat()),
        )
    account = account_by_id(conn, int(cur.lastrowid or 0))
    if account is None:  # pragma: no cover
        raise RuntimeError("insert returned no account")
    return account


def accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute("SELECT * FROM nw_accounts ORDER BY kind, name COLLATE NOCASE")
    return [_account(r) for r in rows]


def account_by_id(conn: sqlite3.Connection, account_id: int) -> Account | None:
    row = conn.execute("SELECT * FROM nw_accounts WHERE id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def account_by_name(conn: sqlite3.Connection, name: str) -> Account | None:
    row = conn.execute("SELECT * FROM nw_accounts WHERE name = ?", (name.strip(),)).fetchone()
    return _account(row) if row else None


def delete_account(conn: sqlite3.Connection, account_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM nw_accounts WHERE id = ?", (account_id,)).rowcount > 0


def to_cents(amount: float) -> int:
    if amount < 0:
        raise NetWorthError("Enter amounts as positive numbers; the account's kind sets the sign.")
    if amount > MAX_AMOUNT:
        raise NetWorthError("Amount is too large.")
    return round(amount * 100)


def check_date(as_of: date, today: date) -> None:
    if as_of > today:
        raise NetWorthError(f"Date {as_of} is in the future. Balances record what was true.")


def upsert_balance(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    account_id: int,
    as_of: date,
    amount_cents: int,
    source: str,
    now: datetime,
) -> tuple[Balance, bool]:
    """Store a balance; a second entry for the same account and date replaces the first.

    Returns the balance and whether it replaced an existing one. Does not commit by itself when
    called inside an outer transaction.
    """
    existed = (
        conn.execute(
            "SELECT 1 FROM nw_balances WHERE account_id = ? AND as_of = ?",
            (account_id, as_of.isoformat()),
        ).fetchone()
        is not None
    )
    conn.execute(
        """
        INSERT INTO nw_balances (account_id, as_of, amount_cents, source, entered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (account_id, as_of) DO UPDATE SET
            amount_cents = excluded.amount_cents,
            source = excluded.source,
            entered_at = excluded.entered_at
        """,
        (account_id, as_of.isoformat(), amount_cents, source, now.isoformat()),
    )
    row = conn.execute(
        "SELECT * FROM nw_balances WHERE account_id = ? AND as_of = ?",
        (account_id, as_of.isoformat()),
    ).fetchone()
    return _balance(row), existed


def balances(conn: sqlite3.Connection, account_id: int | None = None) -> list[Balance]:
    if account_id is None:
        rows = conn.execute("SELECT * FROM nw_balances ORDER BY as_of, account_id")
    else:
        rows = conn.execute(
            "SELECT * FROM nw_balances WHERE account_id = ? ORDER BY as_of", (account_id,)
        )
    return [_balance(r) for r in rows]


def delete_balance(conn: sqlite3.Connection, balance_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM nw_balances WHERE id = ?", (balance_id,)).rowcount > 0
