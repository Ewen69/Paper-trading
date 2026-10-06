"""Performance metrics and bootstrap confidence intervals.

The bootstrap resamples the backtest's own daily returns; it never invents prices. The daily
series uses a moving-block bootstrap, which keeps short-range autocorrelation (block length
about n^(1/3) sessions). Strategy and benchmark are resampled on the same blocks, so their
difference has an honest interval. Trade statistics use an i.i.d. bootstrap over trades.
A fixed seed makes every run reproducible; the seed is reported.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from ptl.backtest.models import EngineResult, EquityPoint

TRADING_DAYS = 252
CONFIDENCE = 0.95
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20251005
MIN_SESSIONS_FOR_CI = 30
MIN_TRADES_FOR_CI = 5
_CHUNK = 250

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


@dataclass(frozen=True, slots=True)
class Estimate:
    value: float | None
    ci: Interval | None = None


@dataclass(frozen=True, slots=True)
class Performance:
    sessions: int
    final_equity: float
    total_return: float
    annualized_return: Estimate
    annual_volatility: float | None
    sharpe: Estimate
    max_drawdown: float
    max_drawdown_date: date | None
    exposure: float
    trade_count: int
    win_rate: Estimate
    expectancy: Estimate  # historical average P&L per trade, after costs
    avg_win: float | None
    avg_loss: float | None
    total_costs: float


@dataclass(frozen=True, slots=True)
class BootstrapInfo:
    method: str
    resamples: int
    block_length: int
    seed: int
    confidence: float


def block_length(sessions: int) -> int:
    return max(1, round(math.pow(sessions, 1 / 3)))


def daily_returns(initial_capital: float, equity: Sequence[EquityPoint]) -> FloatArray:
    values = np.array([initial_capital, *(p.equity for p in equity)], dtype=np.float64)
    returns: FloatArray = values[1:] / values[:-1] - 1.0
    return returns


def _annualized(returns: FloatArray) -> float:
    growth = float(np.prod(1.0 + returns))
    return growth ** (TRADING_DAYS / len(returns)) - 1.0 if growth > 0 else -1.0


def _sharpe(returns: FloatArray) -> float | None:
    if len(returns) < 2:  # noqa: PLR2004
        return None
    std = float(np.std(returns, ddof=1))
    return float(np.mean(returns)) / std * math.sqrt(TRADING_DAYS) if std > 0 else None


def _percentile_interval(samples: FloatArray) -> Interval | None:
    finite = samples[np.isfinite(samples)]
    if len(finite) == 0:
        return None
    tail = (1 - CONFIDENCE) / 2 * 100
    low, high = np.percentile(finite, [tail, 100 - tail])
    return Interval(float(low), float(high))


def _block_indices(
    n: int, block: int, count: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(count, blocks))
    indices: npt.NDArray[np.int64] = (starts[:, :, None] + np.arange(block)).reshape(count, -1)
    return indices[:, :n]


def _resampled_stats(resampled: FloatArray) -> tuple[FloatArray, FloatArray]:
    n = resampled.shape[1]
    log_growth = np.sum(np.log1p(np.maximum(resampled, -0.999999)), axis=1)
    annualized: FloatArray = np.exp(log_growth * TRADING_DAYS / n) - 1.0
    std = np.std(resampled, axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe: FloatArray = np.where(
            std > 0, np.mean(resampled, axis=1) / std * math.sqrt(TRADING_DAYS), np.nan
        )
    return annualized, sharpe


@dataclass(frozen=True, slots=True)
class PairedBootstrap:
    """CIs for both series' annualized return and Sharpe, plus their annualized-return gap."""

    strategy_annualized: Interval | None
    strategy_sharpe: Interval | None
    benchmark_annualized: Interval | None
    benchmark_sharpe: Interval | None
    excess_annualized: Interval | None


def paired_bootstrap(strategy: FloatArray, benchmark: FloatArray) -> PairedBootstrap:
    if len(strategy) != len(benchmark):
        raise ValueError("paired series must have the same length")
    n = len(strategy)
    if n < MIN_SESSIONS_FOR_CI:
        return PairedBootstrap(None, None, None, None, None)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    block = block_length(n)
    collected: dict[str, list[FloatArray]] = {k: [] for k in ("sa", "ss", "ba", "bs")}
    for done in range(0, BOOTSTRAP_RESAMPLES, _CHUNK):
        idx = _block_indices(n, block, min(_CHUNK, BOOTSTRAP_RESAMPLES - done), rng)
        sa, ss = _resampled_stats(strategy[idx])
        ba, bs = _resampled_stats(benchmark[idx])
        for key, arr in (("sa", sa), ("ss", ss), ("ba", ba), ("bs", bs)):
            collected[key].append(arr)
    s_ann, s_sh, b_ann, b_sh = (np.concatenate(collected[k]) for k in ("sa", "ss", "ba", "bs"))
    return PairedBootstrap(
        _percentile_interval(s_ann),
        _percentile_interval(s_sh),
        _percentile_interval(b_ann),
        _percentile_interval(b_sh),
        _percentile_interval(s_ann - b_ann),
    )


def _trade_bootstrap(pnls: FloatArray) -> tuple[Interval | None, Interval | None]:
    if len(pnls) < MIN_TRADES_FOR_CI:
        return None, None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = pnls[rng.integers(0, len(pnls), size=(BOOTSTRAP_RESAMPLES, len(pnls)))]
    win_rates: FloatArray = np.mean(samples > 0, axis=1).astype(np.float64)
    expectancies: FloatArray = np.mean(samples, axis=1)
    return _percentile_interval(win_rates), _percentile_interval(expectancies)


def performance(
    result: EngineResult,
    annualized_ci: Interval | None = None,
    sharpe_ci: Interval | None = None,
) -> Performance:
    returns = daily_returns(result.initial_capital, result.equity)
    values = np.array([p.equity for p in result.equity], dtype=np.float64)
    peaks = np.maximum.accumulate(np.concatenate(([result.initial_capital], values)))[1:]
    drawdowns = values / peaks - 1.0
    worst = int(np.argmin(drawdowns))
    pnls = np.array([t.pnl for t in result.trades], dtype=np.float64)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    win_ci, exp_ci = _trade_bootstrap(pnls)
    return Performance(
        sessions=len(result.equity),
        final_equity=float(values[-1]),
        total_return=float(values[-1] / result.initial_capital - 1.0),
        annualized_return=Estimate(_annualized(returns), annualized_ci),
        annual_volatility=(
            float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS)) if len(returns) > 1 else None
        ),
        sharpe=Estimate(_sharpe(returns), sharpe_ci),
        max_drawdown=float(drawdowns[worst]),
        max_drawdown_date=result.equity[worst].day if drawdowns[worst] < 0 else None,
        exposure=float(np.mean([p.exposure > 0 for p in result.equity])),
        trade_count=len(pnls),
        win_rate=Estimate(float(np.mean(pnls > 0)) if len(pnls) else None, win_ci),
        expectancy=Estimate(float(np.mean(pnls)) if len(pnls) else None, exp_ci),
        avg_win=float(np.mean(wins)) if len(wins) else None,
        avg_loss=float(np.mean(losses)) if len(losses) else None,
        total_costs=result.total_costs,
    )


def bootstrap_info(sessions: int) -> BootstrapInfo:
    return BootstrapInfo(
        method="moving-block bootstrap of daily returns (paired); i.i.d. bootstrap of trades",
        resamples=BOOTSTRAP_RESAMPLES,
        block_length=block_length(sessions),
        seed=BOOTSTRAP_SEED,
        confidence=CONFIDENCE,
    )
