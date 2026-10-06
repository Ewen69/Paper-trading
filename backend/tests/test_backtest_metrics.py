import math
from datetime import date, timedelta

import numpy as np
import pytest

from ptl.backtest.metrics import (
    BOOTSTRAP_SEED,
    TRADING_DAYS,
    daily_returns,
    paired_bootstrap,
    performance,
)
from ptl.backtest.models import EngineResult, EquityPoint, Trade

D0 = date(2025, 1, 6)


def _result(values: list[float], trades: list[Trade] | None = None) -> EngineResult:
    points = [EquityPoint(D0 + timedelta(i), v, 1.0) for i, v in enumerate(values)]
    return EngineResult(100.0, points, [], trades or [])


def _trade(pnl: float) -> Trade:
    return Trade(D0, D0, 100.0, 100.0 + pnl, 1, "signal")


def test_hand_computed_drawdown_and_returns() -> None:
    perf = performance(_result([120.0, 90.0, 135.0]))
    assert perf.total_return == pytest.approx(0.35)
    assert perf.max_drawdown == pytest.approx(90 / 120 - 1)
    assert perf.max_drawdown_date == D0 + timedelta(1)
    returns = daily_returns(100.0, _result([120.0, 90.0, 135.0]).equity)
    assert list(returns) == pytest.approx([0.2, -0.25, 0.5])
    assert perf.annualized_return.value == pytest.approx(1.35 ** (TRADING_DAYS / 3) - 1)
    std = float(np.std([0.2, -0.25, 0.5], ddof=1))
    assert perf.sharpe.value == pytest.approx(np.mean([0.2, -0.25, 0.5]) / std * math.sqrt(252))


def test_no_drawdown_and_zero_volatility() -> None:
    perf = performance(_result([101.0, 102.01]))
    assert perf.max_drawdown == 0
    assert perf.max_drawdown_date is None
    flat = performance(_result([100.0, 100.0, 100.0]))
    assert flat.sharpe.value is None  # zero volatility: Sharpe is undefined, not infinite


def test_trade_stats() -> None:
    trades = [_trade(p) for p in (10, -5, 20, -5, 0)]
    perf = performance(_result([110.0], trades))
    assert perf.trade_count == 5
    assert perf.win_rate.value == pytest.approx(0.4)  # a zero-P&L trade is not a win
    assert perf.expectancy.value == pytest.approx(4.0)
    assert perf.avg_win == pytest.approx(15.0)
    assert perf.avg_loss == pytest.approx(-10 / 3)
    assert perf.win_rate.ci is not None
    assert perf.expectancy.ci is not None
    assert perf.expectancy.ci.low <= 4.0 <= perf.expectancy.ci.high


def test_too_few_trades_have_no_interval() -> None:
    perf = performance(_result([110.0], [_trade(10), _trade(-1)]))
    assert perf.win_rate.ci is None
    assert perf.expectancy.ci is None


def test_paired_bootstrap_is_deterministic_and_sensible() -> None:
    rng = np.random.default_rng(1)  # test input only
    bench = rng.normal(0.0004, 0.01, 500)
    strat = bench * 0.5
    first = paired_bootstrap(strat, bench)
    second = paired_bootstrap(strat, bench)
    assert first == second  # fixed seed BOOTSTRAP_SEED
    assert BOOTSTRAP_SEED == 20251005
    assert first.excess_annualized is not None
    assert first.strategy_sharpe is not None
    assert first.benchmark_sharpe is not None
    # Half the exposure: same Sharpe in every resample, so the two intervals coincide.
    assert first.strategy_sharpe.low == pytest.approx(first.benchmark_sharpe.low)


def test_paired_bootstrap_needs_enough_sessions() -> None:
    short = np.zeros(10)
    assert paired_bootstrap(short, short).excess_annualized is None
    with pytest.raises(ValueError, match="same length"):
        paired_bootstrap(np.zeros(40), np.zeros(41))
