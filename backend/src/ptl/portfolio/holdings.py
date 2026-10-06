"""Manual holdings: storage, validation and all-or-nothing CSV import/export. Local SQLite only.

CSV header (one holding per row; option columns blank unless asset_class is option):

    symbol,asset_class,quantity,account,option_type,strike,expiration,manual_price,manual_price_as_of
    VTI,etf,120,Brokerage,,,,,
    SPY,option,-1,Brokerage,put,540,2026-12-18,,
    House fund,fund,1,401k,,,,98000,2026-09-30

Quantity is shares (or dollars for cash, or contracts for options; negative = short option).
A manual price is used only when no imported price exists for the symbol.
"""

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

AssetClass = Literal["stock", "etf", "fund", "bond", "cash", "option", "crypto", "other"]
ASSET_CLASSES: tuple[AssetClass, ...] = (
    "stock",
    "etf",
    "fund",
    "bond",
    "cash",
    "option",
    "crypto",
    "other",
)
HEADER = (
    "symbol",
    "asset_class",
    "quantity",
    "account",
    "option_type",
    "strike",
    "expiration",
    "manual_price",
    "manual_price_as_of",
)
_SYMBOL = re.compile(r"^[A-Za-z0-9 .\-/]{1,40}$")
MAX_ERRORS_SHOWN = 20


class HoldingError(ValueError):
    """A holding that would be stored ambiguously or invalidly."""


@dataclass(frozen=True, slots=True)
class Holding:
    id: int
    symbol: str
    asset_class: AssetClass
    quantity: float
    account: str
    option_type: Literal["call", "put"] | None
    strike: float | None
    expiration: date | None
    manual_price: float | None
    manual_price_as_of: date | None
    note: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HoldingIn:
    symbol: str
    asset_class: str
    quantity: float
    account: str = ""
    option_type: str | None = None
    strike: float | None = None
    expiration: date | None = None
    manual_price: float | None = None
    manual_price_as_of: date | None = None
    note: str = ""


def _row(r: sqlite3.Row) -> Holding:
    return Holding(
        id=r["id"],
        symbol=r["symbol"],
        asset_class=r["asset_class"],
        quantity=r["quantity"],
        account=r["account"],
        option_type=r["option_type"],
        strike=r["strike"],
        expiration=date.fromisoformat(r["expiration"]) if r["expiration"] else None,
        manual_price=r["manual_price"],
        manual_price_as_of=(
            date.fromisoformat(r["manual_price_as_of"]) if r["manual_price_as_of"] else None
        ),
        note=r["note"],
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def validate(h: HoldingIn, today: date) -> HoldingIn:  # noqa: PLR0912 - one check per rule
    symbol = h.symbol.strip()
    asset_class = h.asset_class.strip().lower()
    if not _SYMBOL.match(symbol):
        raise HoldingError(f"Symbol {symbol!r} must be 1-40 letters, digits, spaces or .-/")
    if asset_class not in ASSET_CLASSES:
        raise HoldingError(f"Asset class must be one of: {', '.join(ASSET_CLASSES)}.")
    if h.quantity == 0:
        raise HoldingError("Quantity can't be 0; delete the holding instead.")
    is_option = asset_class == "option"
    if is_option:
        if h.option_type not in ("call", "put"):
            raise HoldingError("An option needs option_type 'call' or 'put'.")
        if h.strike is None or h.strike <= 0:
            raise HoldingError("An option needs a positive strike.")
        if h.expiration is None:
            raise HoldingError("An option needs an expiration date.")
        if h.quantity != int(h.quantity):
            raise HoldingError("Option quantity is whole contracts.")
    else:
        if h.option_type or h.strike is not None or h.expiration is not None:
            raise HoldingError("Only options take option_type, strike and expiration.")
        if h.quantity < 0:
            raise HoldingError("Only options can be short here; enter a positive quantity.")
    if (h.manual_price is None) != (h.manual_price_as_of is None):
        raise HoldingError("A manual price needs its date, and a date needs its price.")
    if h.manual_price is not None and h.manual_price < 0:
        raise HoldingError("Manual price can't be negative.")
    if h.manual_price_as_of is not None and h.manual_price_as_of > today:
        raise HoldingError(f"Manual price date {h.manual_price_as_of} is in the future.")
    return HoldingIn(
        symbol=symbol.upper() if asset_class in ("stock", "etf", "option") else symbol,
        asset_class=asset_class,
        quantity=h.quantity,
        account=h.account.strip()[:80],
        option_type=h.option_type if is_option else None,
        strike=h.strike if is_option else None,
        expiration=h.expiration if is_option else None,
        manual_price=h.manual_price,
        manual_price_as_of=h.manual_price_as_of,
        note=h.note.strip()[:200],
    )


def _insert(conn: sqlite3.Connection, h: HoldingIn, now: datetime) -> int:
    cur = conn.execute(
        """
        INSERT INTO pf_holdings (symbol, asset_class, quantity, account, option_type, strike,
            expiration, manual_price, manual_price_as_of, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            h.symbol,
            h.asset_class,
            h.quantity,
            h.account,
            h.option_type,
            h.strike,
            h.expiration.isoformat() if h.expiration else None,
            h.manual_price,
            h.manual_price_as_of.isoformat() if h.manual_price_as_of else None,
            h.note,
            now.isoformat(),
        ),
    )
    return int(cur.lastrowid or 0)


def add_holding(conn: sqlite3.Connection, h: HoldingIn, now: datetime) -> Holding:
    clean = validate(h, now.date())
    with conn:
        new_id = _insert(conn, clean, now)
    row = conn.execute("SELECT * FROM pf_holdings WHERE id = ?", (new_id,)).fetchone()
    return _row(row)


def holdings(conn: sqlite3.Connection) -> list[Holding]:
    rows = conn.execute("SELECT * FROM pf_holdings ORDER BY account, symbol, id")
    return [_row(r) for r in rows]


def delete_holding(conn: sqlite3.Connection, holding_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM pf_holdings WHERE id = ?", (holding_id,)).rowcount > 0


class CsvRejectedError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        shown = errors[:MAX_ERRORS_SHOWN]
        more = len(errors) - len(shown)
        tail = f"\n... and {more} more" if more else ""
        super().__init__("Import rejected; nothing was saved.\n" + "\n".join(shown) + tail)
        self.errors = errors


def _opt_float(text: str) -> float | None:
    return float(text) if text.strip() else None


def _opt_date(text: str) -> date | None:
    return date.fromisoformat(text.strip()) if text.strip() else None


def import_csv(
    conn: sqlite3.Connection, text: str, *, replace: bool, now: datetime
) -> tuple[int, int]:
    """Import holdings; with replace=True the file replaces every manual holding.

    Returns (imported, removed).
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if header is None or tuple(h.strip().lower() for h in header) != HEADER:
        raise CsvRejectedError([f"Line 1: header must be exactly {','.join(HEADER)}."])
    rows: list[HoldingIn] = []
    errors: list[str] = []
    for line, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        if len(raw) != len(HEADER):
            errors.append(f"Line {line}: expected {len(HEADER)} columns, got {len(raw)}.")
            continue
        symbol, asset_class, qty, account, opt_type, strike, exp, price, price_date = raw
        try:
            rows.append(
                validate(
                    HoldingIn(
                        symbol=symbol,
                        asset_class=asset_class,
                        quantity=float(qty),
                        account=account,
                        option_type=opt_type.strip().lower() or None,
                        strike=_opt_float(strike),
                        expiration=_opt_date(exp),
                        manual_price=_opt_float(price),
                        manual_price_as_of=_opt_date(price_date),
                    ),
                    now.date(),
                )
            )
        except HoldingError as exc:
            errors.append(f"Line {line}: {exc}")
        except ValueError:
            errors.append(f"Line {line}: numbers must be plain numbers and dates YYYY-MM-DD.")
    if not rows and not errors:
        errors.append("The file has no data rows.")
    if errors:
        raise CsvRejectedError(errors)
    with conn:
        removed = conn.execute("DELETE FROM pf_holdings").rowcount if replace else 0
        for h in rows:
            _insert(conn, h, now)
    return len(rows), removed


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:g}"


def export_csv(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for h in holdings(conn):
        writer.writerow(
            [
                h.symbol,
                h.asset_class,
                _fmt(h.quantity),
                h.account,
                h.option_type or "",
                _fmt(h.strike),
                h.expiration.isoformat() if h.expiration else "",
                _fmt(h.manual_price),
                h.manual_price_as_of.isoformat() if h.manual_price_as_of else "",
            ]
        )
    return out.getvalue()
