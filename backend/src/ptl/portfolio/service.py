"""Values holdings from stored real data and builds the analysis and rule-based tips.

Price for a stock/ETF/fund/bond/crypto/other holding, in order:
  1. the newest imported end-of-day bar close for the symbol (any dataset);
  2. the holding's manual price (with its date);
  3. otherwise unpriced: excluded from totals and listed, never guessed.
Cash is face value. An option is valued at what closing it would get on its newest imported
quote: bid for a long, ask for a short (x100 x contracts). Paper positions come read-only from
the Alpaca paper account (simulated money) and are a separate book.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from ptl.backtest import repository as bt_repo
from ptl.data.freshness import dataset_staleness
from ptl.market_calendar import MarketCalendar
from ptl.paper.broker import BrokerPosition
from ptl.portfolio import metrics
from ptl.portfolio.holdings import Holding

Book = Literal["manual", "paper", "combined"]
MULTIPLIER = 100
OCC_MIN_LENGTH = 10  # equity tickers are shorter than OCC option symbols

# Rule thresholds. Tips state the observed value and the threshold; they are rules, not advice.
TOP_WEIGHT = 0.20
MIN_EFFECTIVE_N = 5.0
MAX_VOLATILITY = 0.25
MAX_BETA = 1.3
MIN_COVERAGE = 0.80

PAPER_SOURCE = "Alpaca paper account (simulated money, read-only)"


@dataclass(frozen=True, slots=True)
class Greeks:
    delta: float  # share-equivalents for the whole position
    theta: float  # dollars per day for the whole position
    vega: float  # dollars per 1 IV point (as the vendor quotes vega) for the whole position


@dataclass(frozen=True, slots=True)
class Position:
    key: str
    book: Literal["manual", "paper"]
    holding_id: int | None
    symbol: str
    label: str
    asset_class: str
    account: str
    quantity: float
    price: float | None
    price_source: str | None
    price_as_of: date | None
    stale: bool
    stale_reason: str | None
    value: float | None
    greeks: Greeks | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tip:
    id: str
    title: str
    status: Literal["fired", "clear", "not_evaluated"]
    observed: str
    threshold: str
    detail: str


@dataclass(slots=True)
class Analysis:
    book: Book
    positions: list[Position]
    total_value: float
    long_value: float
    allocation: list[tuple[str, float, float]]  # (asset class, value, weight of net total)
    concentration: metrics.Concentration | None
    risk: metrics.RiskResult | None
    risk_note: str
    risk_included: list[str]
    risk_excluded: list[str]
    coverage: float | None
    price_basis: str
    greeks_total: Greeks | None
    greeks_missing: list[str]
    tips: list[Tip] = field(default_factory=list)


# ---- pricing ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Bar:
    dataset_id: int
    session: date
    close: float


def latest_bar(conn: sqlite3.Connection, symbol: str) -> _Bar | None:
    row = conn.execute(
        "SELECT dataset_id, session_date, close FROM equity_bars WHERE symbol = ? "
        "ORDER BY session_date DESC, dataset_id DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return _Bar(row["dataset_id"], date.fromisoformat(row["session_date"]), row["close"])


def latest_quote(conn: sqlite3.Connection, h: Holding) -> sqlite3.Row | None:
    if h.expiration is None or h.strike is None:
        return None
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM option_quotes WHERE underlying = ? AND expiration = ? "
        "AND ABS(strike - ?) < 1e-6 AND option_type = ? "
        "ORDER BY quote_date DESC, dataset_id DESC LIMIT 1",
        (h.symbol, h.expiration.isoformat(), h.strike, h.option_type),
    ).fetchone()
    return row


@dataclass(frozen=True, slots=True)
class PricingContext:
    now: datetime
    calendar: MarketCalendar
    stale_after_sessions: int
    manual_stale_after_days: int


def _manual(  # noqa: PLR0913
    h: Holding,
    label: str,
    *,
    price: float | None,
    price_source: str | None,
    price_as_of: date | None,
    value: float | None,
    stale: bool = False,
    stale_reason: str | None = None,
    greeks: Greeks | None = None,
    notes: tuple[str, ...] = (),
) -> Position:
    return Position(
        key=f"manual-{h.id}",
        book="manual",
        holding_id=h.id,
        symbol=h.symbol,
        label=label,
        asset_class=h.asset_class,
        account=h.account,
        quantity=h.quantity,
        price=price,
        price_source=price_source,
        price_as_of=price_as_of,
        stale=stale,
        stale_reason=stale_reason,
        value=value,
        greeks=greeks,
        notes=notes,
    )


def _value_option(conn: sqlite3.Connection, h: Holding, ctx: PricingContext) -> Position:
    label = f"{h.symbol} {h.expiration} {h.strike:g} {h.option_type}"
    notes: list[str] = []
    if h.expiration is not None and h.expiration < ctx.now.date():
        notes.append("Expired: update or remove this holding.")
    quote = latest_quote(conn, h)
    if quote is None:
        return _manual(
            h,
            label,
            price=None,
            price_source=None,
            price_as_of=None,
            stale=False,
            stale_reason=None,
            value=None,
            notes=(*notes, "No imported quote for this contract."),
        )
    long = h.quantity > 0
    price = float(quote["bid"] if long else quote["ask"])
    quote_date = date.fromisoformat(quote["quote_date"])
    staleness = dataset_staleness(quote_date, ctx.now, ctx.calendar, ctx.stale_after_sessions)
    greeks = None
    if quote["delta"] is not None and quote["theta"] is not None and quote["vega"] is not None:
        scale = MULTIPLIER * h.quantity
        greeks = Greeks(quote["delta"] * scale, quote["theta"] * scale, quote["vega"] * scale)
    side = "bid (a long closes by selling)" if long else "ask (a short closes by buying)"
    return _manual(
        h,
        label,
        price=price,
        price_source=f"Imported option quote, dataset #{quote['dataset_id']}, at the {side}",
        price_as_of=quote_date,
        stale=staleness.stale,
        stale_reason=staleness.reason,
        value=price * MULTIPLIER * h.quantity,
        greeks=greeks,
        notes=tuple(notes) + (() if greeks else ("No Greeks in the imported quote.",)),
    )


def value_holding(conn: sqlite3.Connection, h: Holding, ctx: PricingContext) -> Position:
    if h.asset_class == "option":
        return _value_option(conn, h, ctx)
    label = h.symbol
    if h.asset_class == "cash":
        return _manual(
            h,
            label,
            price=1.0,
            price_source="Cash at face value",
            price_as_of=ctx.now.date(),
            stale=False,
            stale_reason=None,
            value=h.quantity,
        )
    bar = latest_bar(conn, h.symbol)
    if bar is not None:
        staleness = dataset_staleness(bar.session, ctx.now, ctx.calendar, ctx.stale_after_sessions)
        return _manual(
            h,
            label,
            price=bar.close,
            price_source=f"Imported end-of-day close, dataset #{bar.dataset_id}",
            price_as_of=bar.session,
            stale=staleness.stale,
            stale_reason=staleness.reason,
            value=bar.close * h.quantity,
        )
    if h.manual_price is not None and h.manual_price_as_of is not None:
        age = (ctx.now.date() - h.manual_price_as_of).days
        stale = age > ctx.manual_stale_after_days
        return _manual(
            h,
            label,
            price=h.manual_price,
            price_source="Manual price you entered",
            price_as_of=h.manual_price_as_of,
            stale=stale,
            stale_reason=f"Manual price is {age} days old." if stale else None,
            value=h.manual_price * h.quantity,
        )
    return _manual(
        h,
        label,
        price=None,
        price_source=None,
        price_as_of=None,
        stale=False,
        stale_reason=None,
        value=None,
        notes=("No imported bars and no manual price: not valued.",),
    )


def paper_positions(positions: dict[str, BrokerPosition], now: datetime) -> list[Position]:
    out = []
    for p in sorted(positions.values(), key=lambda x: x.symbol):
        is_option = len(p.symbol) > OCC_MIN_LENGTH
        out.append(
            Position(
                key=f"paper-{p.symbol}",
                book="paper",
                holding_id=None,
                symbol=p.symbol,
                label=p.symbol,
                asset_class="option" if is_option else "stock",
                account="Alpaca paper",
                quantity=p.qty,
                price=p.market_value / p.qty if p.qty else None,
                price_source=PAPER_SOURCE,
                price_as_of=now.date(),
                stale=False,
                stale_reason=None,
                value=p.market_value,
                notes=("Simulated money.",),
            )
        )
    return out


# ---- analysis ---------------------------------------------------------------------------

RISK_CLASSES = frozenset({"stock", "etf", "fund", "bond", "crypto", "other"})


def _history(conn: sqlite3.Connection, symbol: str) -> tuple[dict[date, float], bool] | None:
    """Price series from the dataset with the newest data for this symbol; (series, adjusted)."""
    entries = [e for e in bt_repo.equity_universe(conn) if e.symbol == symbol]
    if not entries:
        return None
    best = max(entries, key=lambda e: (e.last_date, e.dataset_id))
    bars = bt_repo.load_equity_bars(conn, best.dataset_id, symbol)
    adjusted = all(b.adj_close is not None for b in bars)
    pairs = [(b.session_date, (b.adj_close if adjusted and b.adj_close else b.close)) for b in bars]
    return metrics.series_from(pairs), adjusted


def analyze(  # noqa: PLR0912, PLR0913, PLR0915 - assembles every section in one pass
    conn: sqlite3.Connection,
    positions: Sequence[Position],
    *,
    book: Book,
    benchmark: str,
    window: int,
    load_history: Callable[[sqlite3.Connection, str], tuple[dict[date, float], bool] | None] = (
        _history
    ),
) -> Analysis:
    valued = [p for p in positions if p.value is not None]
    total = sum(p.value for p in valued if p.value is not None)
    long_value = sum(p.value for p in valued if p.value is not None and p.value > 0)

    by_class: dict[str, float] = defaultdict(float)
    for p in valued:
        by_class[p.asset_class] += p.value or 0.0
    allocation = sorted(
        ((k, v, v / total if total else 0.0) for k, v in by_class.items()), key=lambda r: -r[1]
    )

    # Concentration over non-cash long positions, grouped by symbol across accounts and books.
    by_symbol: dict[str, float] = defaultdict(float)
    for p in valued:
        if p.asset_class != "cash" and (p.value or 0) > 0:
            by_symbol[p.label] += p.value or 0.0
    conc = metrics.concentration(by_symbol)

    # Risk replay: equity-like positions with history + cash at 0 return; options excluded.
    weights_value: dict[str, float] = defaultdict(float)
    prices: dict[str, dict[date, float]] = {}
    included: list[str] = []
    excluded: list[str] = []
    unadjusted: list[str] = []
    for p in valued:
        if p.value is None or p.value <= 0:
            if p.asset_class == "option":
                excluded.append(f"{p.label} (option)")
            continue
        if p.asset_class == "cash":
            weights_value["__cash__"] += p.value
            continue
        if p.asset_class not in RISK_CLASSES:
            excluded.append(f"{p.label} ({p.asset_class})")
            continue
        if p.symbol not in prices:
            hist = load_history(conn, p.symbol)
            if hist is None:
                excluded.append(f"{p.label} (no price history)")
                continue
            prices[p.symbol], adjusted = hist
            if not adjusted:
                unadjusted.append(p.symbol)
        weights_value[p.symbol] += p.value
        if p.symbol not in included:
            included.append(p.symbol)
    covered = sum(weights_value.values())
    coverage = covered / long_value if long_value > 0 else None

    bench_hist = load_history(conn, benchmark)
    bench_series = bench_hist[0] if bench_hist else None
    if bench_hist and not bench_hist[1]:
        unadjusted.append(benchmark)
    risk: metrics.RiskResult | None = None
    risk_note = ""
    if covered > 0 and included:
        weights = {k: v / covered for k, v in weights_value.items()}
        try:
            risk = metrics.replay_risk(weights, prices, bench_series, window)
        except metrics.RiskUnavailableError as exc:
            risk_note = str(exc)
        if risk is not None and bench_series is None:
            risk_note = (
                f"No imported bars for benchmark {benchmark}: beta and correlation unavailable."
            )
    else:
        risk_note = "No priced stock, ETF, fund, bond or crypto position with history to replay."
    basis = (
        "Adjusted closes (dividends and splits included)"
        if not unadjusted
        else "Unadjusted closes for "
        + ", ".join(sorted(set(unadjusted)))
        + ": dividends and splits are not reflected, which can distort returns."
    )

    greek_positions = [p for p in positions if p.asset_class == "option" and p.book == "manual"]
    with_greeks = [p.greeks for p in greek_positions if p.greeks is not None]
    greeks_total = (
        Greeks(
            sum(g.delta for g in with_greeks),
            sum(g.theta for g in with_greeks),
            sum(g.vega for g in with_greeks),
        )
        if with_greeks
        else None
    )
    greeks_missing = [p.label for p in greek_positions if p.greeks is None]

    analysis = Analysis(
        book=book,
        positions=list(positions),
        total_value=total,
        long_value=long_value,
        allocation=allocation,
        concentration=conc,
        risk=risk,
        risk_note=risk_note,
        risk_included=included,
        risk_excluded=excluded,
        coverage=coverage,
        price_basis=basis,
        greeks_total=greeks_total,
        greeks_missing=greeks_missing,
    )
    analysis.tips = tips(analysis)
    return analysis


# ---- tips ---------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def tips(a: Analysis) -> list[Tip]:
    out: list[Tip] = []
    c = a.concentration
    if c is None or not c.top:
        out.append(
            Tip(
                "largest_position",
                "Largest position",
                "not_evaluated",
                "no priced non-cash positions",
                f"above {_pct(TOP_WEIGHT)}",
                "",
            )
        )
        out.append(
            Tip(
                "effective_n",
                "Diversification",
                "not_evaluated",
                "no priced non-cash positions",
                f"below {MIN_EFFECTIVE_N:g}",
                "",
            )
        )
    else:
        name, weight = c.top[0]
        out.append(
            Tip(
                "largest_position",
                "Largest position",
                "fired" if weight > TOP_WEIGHT else "clear",
                f"{name} is {_pct(weight)} of non-cash long value",
                f"fires above {_pct(TOP_WEIGHT)}",
                "One position's moves drive much of the result when its weight is high.",
            )
        )
        out.append(
            Tip(
                "effective_n",
                "Diversification",
                "fired" if c.effective_n < MIN_EFFECTIVE_N else "clear",
                f"effective number of positions {c.effective_n:.1f} (HHI {c.hhi:.3f})",
                f"fires below {MIN_EFFECTIVE_N:g}",
                "Effective N = 1 / sum of squared weights; equal weights in N positions give N.",
            )
        )
    if a.risk is None:
        reason = a.risk_note or "no risk replay"
        out.append(
            Tip(
                "volatility",
                "Volatility",
                "not_evaluated",
                reason,
                f"above {_pct(MAX_VOLATILITY)}",
                "",
            )
        )
        out.append(
            Tip("beta", "Beta to benchmark", "not_evaluated", reason, f"above {MAX_BETA:g}", "")
        )
    else:
        r = a.risk
        out.append(
            Tip(
                "volatility",
                "Volatility",
                "fired" if r.volatility > MAX_VOLATILITY else "clear",
                f"{_pct(r.volatility)} annualized over {r.returns} sessions ({r.start} to {r.end})",
                f"fires above {_pct(MAX_VOLATILITY)}",
                "Replays today's weights over past prices; past volatility isn't a forecast.",
            )
        )
        if r.beta is None:
            out.append(
                Tip(
                    "beta",
                    "Beta to benchmark",
                    "not_evaluated",
                    a.risk_note or "no benchmark",
                    f"above {MAX_BETA:g}",
                    "",
                )
            )
        else:
            out.append(
                Tip(
                    "beta",
                    "Beta to benchmark",
                    "fired" if r.beta > MAX_BETA else "clear",
                    f"beta {r.beta:.2f}, correlation {r.correlation:.2f}"
                    if r.correlation is not None
                    else f"beta {r.beta:.2f}",
                    f"fires above {MAX_BETA:g}",
                    "Beta above 1 means the replayed mix moved more than the benchmark on average.",
                )
            )
    if a.coverage is None:
        out.append(
            Tip(
                "risk_coverage",
                "Risk coverage",
                "not_evaluated",
                "no priced positions",
                f"below {_pct(MIN_COVERAGE)}",
                "",
            )
        )
    else:
        out.append(
            Tip(
                "risk_coverage",
                "Risk coverage",
                "fired" if a.coverage < MIN_COVERAGE else "clear",
                f"risk metrics cover {_pct(a.coverage)} of long value",
                f"fires below {_pct(MIN_COVERAGE)}",
                "Excluded: " + (", ".join(a.risk_excluded) if a.risk_excluded else "nothing"),
            )
        )
    stale = [p.label for p in a.positions if p.stale]
    out.append(
        Tip(
            "stale_prices",
            "Stale prices",
            "fired" if stale else "clear",
            f"{len(stale)} stale: {', '.join(stale)}" if stale else "all prices current",
            "fires on any stale price",
            "Import fresh bars or quotes, or update the manual price.",
        )
    )
    unpriced = [p.label for p in a.positions if p.value is None]
    out.append(
        Tip(
            "unpriced",
            "Unpriced holdings",
            "fired" if unpriced else "clear",
            f"{len(unpriced)} not valued: {', '.join(unpriced)}"
            if unpriced
            else "every holding valued",
            "fires on any unpriced holding",
            "Unpriced holdings are left out of every total; nothing is guessed.",
        )
    )
    shorts = [p.label for p in a.positions if p.asset_class == "option" and p.quantity < 0]
    out.append(
        Tip(
            "short_options",
            "Short options",
            "fired" if shorts else "clear",
            f"{len(shorts)} short: {', '.join(shorts)}" if shorts else "no short options",
            "fires on any short option",
            "A short option without a matching long leg can lose far more than its premium. "
            "This app only models defined-risk structures.",
        )
    )
    expired = [p.label for p in a.positions if any(n.startswith("Expired") for n in p.notes)]
    out.append(
        Tip(
            "expired_options",
            "Expired options",
            "fired" if expired else "clear",
            ", ".join(expired) if expired else "none",
            "fires on any option past expiration",
            "An expired contract no longer exists; record what happened to it.",
        )
    )
    return out
