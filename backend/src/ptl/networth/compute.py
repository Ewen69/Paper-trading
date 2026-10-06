"""Net worth from stored balances, plus an assumption-labeled projection range.

Net worth on a date = sum of each asset's latest balance on or before that date, minus the
same for liabilities. A balance is carried forward until the next one is entered; accounts with
no balance yet on that date count as 0 and are listed as missing. Nothing is interpolated.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ptl.networth.store import Account, Balance

PROJECTION_NOTE = (
    "Assumption, not a forecast: compounds today's net worth at a fixed nominal annual rate, "
    "before taxes, fees and inflation, with contributions added at each year end. Real returns "
    "vary and can be negative."
)


class ProjectionError(ValueError):
    """Inputs for which a growth projection would be meaningless."""


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    as_of: date
    assets_cents: int
    liabilities_cents: int
    carried_forward: int  # accounts whose value comes from an earlier date
    missing: tuple[str, ...]  # accounts with no balance yet on this date (counted as 0)

    @property
    def net_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents


@dataclass(frozen=True, slots=True)
class Latest:
    account: Account
    balance: Balance | None
    age_days: int | None
    stale: bool


def latest_balances(
    accounts: Sequence[Account], balances: Sequence[Balance], today: date, stale_after_days: int
) -> list[Latest]:
    newest: dict[int, Balance] = {}
    for b in balances:  # ordered by date
        newest[b.account_id] = b
    out = []
    for a in accounts:
        found = newest.get(a.id)
        age = (today - found.as_of).days if found else None
        out.append(Latest(a, found, age, age is None or age > stale_after_days))
    return out


def history(accounts: Sequence[Account], balances: Sequence[Balance]) -> list[HistoryPoint]:
    """One point per date on which any balance was entered."""
    by_id = {a.id: a for a in accounts}
    by_date: dict[date, list[Balance]] = {}
    for b in balances:
        by_date.setdefault(b.as_of, []).append(b)
    current: dict[int, Balance] = {}
    points = []
    for day in sorted(by_date):
        for b in by_date[day]:
            current[b.account_id] = b
        assets = sum(b.amount_cents for i, b in current.items() if by_id[i].kind == "asset")
        debts = sum(b.amount_cents for i, b in current.items() if by_id[i].kind == "liability")
        carried = sum(1 for b in current.values() if b.as_of < day)
        missing = tuple(a.name for a in accounts if a.id not in current)
        points.append(HistoryPoint(day, assets, debts, carried, missing))
    return points


@dataclass(frozen=True, slots=True)
class ProjectionPoint:
    year: int
    low_cents: int
    high_cents: int


def _grow(start: float, rate: float, contribution: float, years: int) -> float:
    if rate == 0:
        return start + contribution * years
    factor = (1 + rate) ** years
    return start * factor + contribution * (factor - 1) / rate


def project(
    start_cents: int, *, years: int, low: float, high: float, contribution_cents: int
) -> list[ProjectionPoint]:
    if start_cents <= 0:
        raise ProjectionError(
            "Projection needs a positive net worth. Applying a growth rate to a negative net "
            "worth would treat debt as an investment, so nothing is projected."
        )
    if not 1 <= years <= 50:  # noqa: PLR2004
        raise ProjectionError("Years must be between 1 and 50.")
    if not -0.5 <= low <= high <= 0.5:  # noqa: PLR2004
        raise ProjectionError("Rates must satisfy -50% ≤ low ≤ high ≤ 50%.")
    if contribution_cents < 0:
        raise ProjectionError("Contribution must be zero or positive.")
    return [
        ProjectionPoint(
            year,
            round(_grow(start_cents, low, contribution_cents, year)),
            round(_grow(start_cents, high, contribution_cents, year)),
        )
        for year in range(years + 1)
    ]
