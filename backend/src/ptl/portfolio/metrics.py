"""Portfolio math on real stored prices. Pure functions; every assumption is named.

Risk metrics replay today's weights over past sessions ("constant current weights, rebalanced
daily"). That describes how the current mix would have behaved, not how the account actually
did, and past behavior doesn't predict future behavior.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import sqrt

import numpy as np

TRADING_DAYS = 252
MIN_RETURNS = 20


@dataclass(frozen=True, slots=True)
class Concentration:
    hhi: float  # sum of squared weights, 1/N (spread) .. 1 (one position)
    effective_n: float  # 1 / HHI
    top: tuple[tuple[str, float], ...]  # (label, weight), largest first, up to 5
    top5_weight: float


def concentration(values: Mapping[str, float]) -> Concentration | None:
    """Over positive values only. Returns None when there is nothing to measure."""
    positive = {k: v for k, v in values.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return None
    weights = sorted(((k, v / total) for k, v in positive.items()), key=lambda kv: -kv[1])
    hhi = sum(w * w for _, w in weights)
    top = tuple(weights[:5])
    return Concentration(hhi, 1 / hhi, top, sum(w for _, w in top))


@dataclass(frozen=True, slots=True)
class RiskResult:
    start: date
    end: date
    returns: int
    volatility: float  # annualized, sqrt(252) x daily stdev
    max_drawdown: float  # worst peak-to-trough of the replayed series (<= 0)
    benchmark_volatility: float | None
    beta: float | None
    correlation: float | None


class RiskUnavailableError(ValueError):
    """Not enough overlapping history to compute risk honestly."""


def replay_risk(
    weights: Mapping[str, float],
    prices: Mapping[str, Mapping[date, float]],
    benchmark: Mapping[date, float] | None,
    window: int,
) -> RiskResult:
    """Constant-weight replay. `weights` sum to 1; a weight with no price series (cash) earns 0.

    Uses only dates on which every priced asset (and the benchmark, if given) has a price, then
    keeps the last `window` daily returns.
    """
    priced = [s for s in weights if s in prices]
    if not priced:
        raise RiskUnavailableError("No position has price history to replay.")
    dates: set[date] = set.intersection(*(set(prices[s]) for s in priced))
    if benchmark is not None:
        dates &= set(benchmark)
    ordered = sorted(dates)[-(window + 1) :]
    if len(ordered) - 1 < MIN_RETURNS:
        raise RiskUnavailableError(
            f"Only {max(0, len(ordered) - 1)} overlapping daily returns; need at least "
            f"{MIN_RETURNS}. Import more history for every held symbol"
            + (" and the benchmark." if benchmark is not None else ".")
        )
    port = np.zeros(len(ordered) - 1)
    for s in priced:
        series = np.array([prices[s][d] for d in ordered], dtype=float)
        port += weights[s] * (series[1:] / series[:-1] - 1)
    vol = float(np.std(port, ddof=1) * sqrt(TRADING_DAYS))
    curve = np.cumprod(1 + port)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], curve)))[1:]
    drawdown = float(np.min(curve / peaks - 1))
    beta = corr = bench_vol = None
    if benchmark is not None:
        b = np.array([benchmark[d] for d in ordered], dtype=float)
        bret = b[1:] / b[:-1] - 1
        var_b = float(np.var(bret, ddof=1))
        bench_vol = float(np.sqrt(var_b) * sqrt(TRADING_DAYS))
        if var_b > 0 and float(np.std(port, ddof=1)) > 0:
            cov = float(np.cov(port, bret, ddof=1)[0, 1])
            beta = cov / var_b
            corr = float(np.corrcoef(port, bret)[0, 1])
    return RiskResult(
        start=ordered[0],
        end=ordered[-1],
        returns=len(ordered) - 1,
        volatility=vol,
        max_drawdown=min(0.0, drawdown),
        benchmark_volatility=bench_vol,
        beta=beta,
        correlation=corr,
    )


def series_from(bars: Sequence[tuple[date, float]]) -> dict[date, float]:
    return {d: p for d, p in bars if p > 0}
