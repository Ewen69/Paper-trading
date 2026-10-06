"""Data-quality checks. They flag problems; they never repair, fill, or drop data.

Severity guide:
- error:   the row cannot be trusted for prices or fills (crossed market, impossible OHLC).
- warning: plausible but suspicious (gaps, zero volume, repeated quotes, extreme moves).
- info:    worth knowing, usually benign (zero bid on far OTM options, outside calendar range).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from ptl.data.models import EquityBar, LiveQuote, OptionQuote, QualityIssue, Severity
from ptl.market_calendar import MarketCalendar

EXTREME_MOVE_RATIO = 1.5  # close/prev_close above this, or below 1/this, is flagged
REPEATED_QUOTE_RUN = 5  # identical bid/ask on this many consecutive quote dates is flagged
_MAX_RANGES_IN_DETAIL = 3


@dataclass
class _Finding:
    severity: Severity
    description: str
    example: str
    count: int = 0
    first: date = date.max
    last: date = date.min


@dataclass
class _Collector:
    findings: dict[tuple[str, str], _Finding] = field(default_factory=dict)

    def add(  # noqa: PLR0913, PLR0917 - one call per check; explicit args read best
        self,
        check: str,
        severity: Severity,
        symbol: str,
        day: date,
        description: str,
        example: str,
        count: int = 1,
    ) -> None:
        finding = self.findings.setdefault(
            (check, symbol), _Finding(severity, description, f"{day}: {example}")
        )
        finding.count += count
        finding.first = min(finding.first, day)
        finding.last = max(finding.last, day)

    def issues(self) -> list[QualityIssue]:
        severity_rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        issues = [
            QualityIssue(
                check=check,
                severity=f.severity,
                symbol=symbol,
                count=f.count,
                first_date=f.first,
                last_date=f.last,
                detail=f"{f.description} First: {f.example}",
            )
            for (check, symbol), f in self.findings.items()
        ]
        return sorted(issues, key=lambda i: (severity_rank[i.severity], i.check, i.symbol))


def _check_dates(
    c: _Collector, symbol: str, days: Iterable[date], sessions: set[date], cal: MarketCalendar
) -> None:
    for day in days:
        if not cal.covers(day):
            c.add(
                "outside_calendar",
                Severity.INFO,
                symbol,
                day,
                "Date is outside the NYSE calendar range, so it can't be checked for gaps.",
                str(day),
            )
        elif day not in sessions:
            c.add(
                "non_session_date",
                Severity.WARNING,
                symbol,
                day,
                "Row is dated on a day the NYSE was closed.",
                day.strftime("%A"),
            )


def _check_missing_sessions(
    c: _Collector, symbol: str, present: set[date], all_sessions: Sequence[date]
) -> None:
    """Flag sessions between a symbol's first and last date that have no row."""
    if not present:
        return
    first, last = min(present), max(present)
    expected = [s for s in all_sessions if first <= s <= last]
    runs: list[list[date]] = []
    for index, session in enumerate(expected):
        if session in present:
            continue
        if runs and index > 0 and expected[index - 1] == runs[-1][-1]:
            runs[-1].append(session)
        else:
            runs.append([session])
    if not runs:
        return
    missing = sum(len(r) for r in runs)
    shown = ", ".join(
        f"{r[0]}" if len(r) == 1 else f"{r[0]}..{r[-1]} ({len(r)})"
        for r in runs[:_MAX_RANGES_IN_DETAIL]
    )
    more = (
        f" and {len(runs) - _MAX_RANGES_IN_DETAIL} more gaps"
        if len(runs) > _MAX_RANGES_IN_DETAIL
        else ""
    )
    c.add(
        "missing_sessions",
        Severity.WARNING,
        symbol,
        runs[0][0],
        f"{missing} NYSE sessions have no data in {len(runs)} gap(s).",
        f"{shown}{more}",
        count=missing,
    )
    c.findings[("missing_sessions", symbol)].last = runs[-1][-1]


def check_equity_bars(bars: Sequence[EquityBar], cal: MarketCalendar) -> list[QualityIssue]:
    c = _Collector()
    if not bars:
        return []
    all_sessions = cal.sessions(
        min(b.session_date for b in bars), max(b.session_date for b in bars)
    )
    session_set = set(all_sessions)

    by_symbol: dict[str, list[EquityBar]] = defaultdict(list)
    for bar in bars:
        by_symbol[bar.symbol].append(bar)

    for symbol, rows in sorted(by_symbol.items()):
        rows.sort(key=lambda b: b.session_date)
        prev_close: float | None = None
        for b in rows:
            d = b.session_date
            if min(b.open, b.high, b.low, b.close) <= 0:
                c.add(
                    "non_positive_price",
                    Severity.ERROR,
                    symbol,
                    d,
                    "Price is zero or negative.",
                    f"O={b.open} H={b.high} L={b.low} C={b.close}",
                )
            elif b.high < b.low or not (b.low <= b.open <= b.high and b.low <= b.close <= b.high):
                c.add(
                    "inconsistent_ohlc",
                    Severity.ERROR,
                    symbol,
                    d,
                    "Open/close outside the high-low range, or high < low.",
                    f"O={b.open} H={b.high} L={b.low} C={b.close}",
                )
            if b.volume < 0:
                c.add(
                    "negative_volume", Severity.ERROR, symbol, d, "Negative volume.", str(b.volume)
                )
            elif b.volume == 0:
                c.add("zero_volume", Severity.WARNING, symbol, d, "Zero volume.", "volume=0")
            if prev_close is not None and prev_close > 0 and b.close > 0:
                ratio = b.close / prev_close
                if ratio > EXTREME_MOVE_RATIO or ratio < 1 / EXTREME_MOVE_RATIO:
                    c.add(
                        "extreme_move",
                        Severity.WARNING,
                        symbol,
                        d,
                        "Close moved more than 50% up or 33% down in one session. This may "
                        "be an unadjusted split or a bad print.",
                        f"{prev_close} -> {b.close}",
                    )
            prev_close = b.close
        _check_dates(c, symbol, (b.session_date for b in rows), session_set, cal)
        _check_missing_sessions(c, symbol, {b.session_date for b in rows}, all_sessions)
    return c.issues()


def check_option_quotes(  # noqa: PLR0912 - flat list of independent checks
    quotes: Sequence[OptionQuote], cal: MarketCalendar
) -> list[QualityIssue]:
    c = _Collector()
    if not quotes:
        return []
    all_sessions = cal.sessions(
        min(q.quote_date for q in quotes), max(q.quote_date for q in quotes)
    )
    session_set = set(all_sessions)

    by_contract: dict[tuple[str, str, date, float, str], list[OptionQuote]] = defaultdict(list)
    dates_by_underlying: dict[str, set[date]] = defaultdict(set)
    for q in quotes:
        by_contract[q.contract_key].append(q)
        dates_by_underlying[q.underlying].add(q.quote_date)
        s, d = q.underlying, q.quote_date
        label = f"{q.expiration} {q.strike:g} {q.option_type}"
        if q.bid < 0 or q.ask < 0:
            c.add(
                "negative_price",
                Severity.ERROR,
                s,
                d,
                "Negative bid or ask.",
                f"{label} bid={q.bid} ask={q.ask}",
            )
        elif q.bid > q.ask:
            c.add(
                "crossed_market",
                Severity.ERROR,
                s,
                d,
                "Bid above ask (crossed market). Not fillable as quoted.",
                f"{label} bid={q.bid} ask={q.ask}",
            )
        elif q.bid == q.ask and q.ask > 0:
            c.add(
                "locked_market",
                Severity.WARNING,
                s,
                d,
                "Bid equals ask (locked market).",
                f"{label} bid=ask={q.ask}",
            )
        if q.ask == 0:
            c.add(
                "no_offer",
                Severity.WARNING,
                s,
                d,
                "Ask is zero (no offer). Can't be bought at this quote.",
                label,
            )
        elif q.bid == 0:
            c.add(
                "zero_bid",
                Severity.INFO,
                s,
                d,
                "Bid is zero. Can't be sold at this quote (common for far out-of-the-money "
                "options).",
                label,
            )
        if q.strike <= 0:
            c.add("non_positive_strike", Severity.ERROR, s, d, "Strike is zero or negative.", label)
        if q.quote_date > q.expiration:
            c.add(
                "quote_after_expiration",
                Severity.ERROR,
                s,
                d,
                "Quote is dated after the contract expired.",
                label,
            )
        if q.underlying_price is not None and q.underlying_price <= 0:
            c.add(
                "non_positive_underlying",
                Severity.ERROR,
                s,
                d,
                "Underlying price is zero or negative.",
                f"{label} underlying={q.underlying_price}",
            )

    for key, rows in by_contract.items():
        rows.sort(key=lambda q: q.quote_date)
        run = 1
        for prev, cur in pairwise(rows):
            same = cur.bid == prev.bid and cur.ask == prev.ask and cur.ask > 0
            run = run + 1 if same else 1
            if run == REPEATED_QUOTE_RUN:
                c.add(
                    "repeated_quote",
                    Severity.WARNING,
                    key[0],
                    cur.quote_date,
                    f"Identical bid/ask on {REPEATED_QUOTE_RUN}+ consecutive quote dates. "
                    "This may be a stale quote carried forward.",
                    f"{key[2]} {key[3]:g} {key[4]} bid={cur.bid} ask={cur.ask}",
                )

    for underlying, days in sorted(dates_by_underlying.items()):
        _check_dates(c, underlying, sorted(days), session_set, cal)
        _check_missing_sessions(c, underlying, days, all_sessions)
    return c.issues()


def check_live_quote(quote: LiveQuote) -> list[QualityIssue]:
    c = _Collector()
    s, d = quote.symbol, quote.timestamp.date()
    sides = f"bid={quote.bid} ask={quote.ask}"
    if quote.bid < 0 or quote.ask < 0:
        c.add("negative_price", Severity.ERROR, s, d, "Negative bid or ask.", sides)
    elif quote.bid > 0 and quote.ask > 0 and quote.bid > quote.ask:
        c.add("crossed_market", Severity.ERROR, s, d, "Bid above ask (crossed market).", sides)
    elif quote.bid == quote.ask and quote.ask > 0:
        c.add("locked_market", Severity.WARNING, s, d, "Bid equals ask (locked market).", sides)
    if quote.ask == 0:
        c.add("no_offer", Severity.WARNING, s, d, "No ask in this quote.", sides)
    if quote.bid == 0:
        c.add("no_bid", Severity.WARNING, s, d, "No bid in this quote.", sides)
    return c.issues()
