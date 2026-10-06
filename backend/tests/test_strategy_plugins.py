from datetime import date, timedelta
from typing import ClassVar

import pytest

from ptl.backtest.models import History, PriceBar
from ptl.backtest.strategies import STRATEGIES, SmaCrossover
from ptl.options.strategies import OPTIONS_STRATEGIES
from ptl.strategy.base import (
    REGISTRY,
    EquityStrategy,
    ParamSpec,
    equity_strategies,
    option_strategies,
    register,
)
from ptl.strategy.plugins.trend_filter import TrendFilter


def history(closes: list[float]) -> History:
    bars = [
        PriceBar(date(2025, 1, 1) + timedelta(days=i), c, c, c, c) for i, c in enumerate(closes)
    ]
    return History(bars, closes, len(bars) - 1)


def test_registry_holds_builtins_and_drop_in_plugins() -> None:
    assert list(equity_strategies()) == ["buy_and_hold", "sma_crossover", "trend_filter"]
    assert list(option_strategies()) == ["put_credit_spread"]
    assert set(STRATEGIES) == set(equity_strategies())  # the backtester sees plugins too
    assert set(OPTIONS_STRATEGIES) == set(option_strategies())


def test_create_validates_params_and_cross_checks() -> None:
    strategy, params = SmaCrossover.create({"fast": 5})
    assert params == {"fast": 5, "slow": 200}
    assert isinstance(strategy, SmaCrossover)
    with pytest.raises(ValueError, match="shorter"):
        SmaCrossover.create({"fast": 50, "slow": 20})
    with pytest.raises(ValueError, match="between"):
        TrendFilter.create({"lookback": 1})


def test_explain_shows_the_arithmetic() -> None:
    # closes 10,11,12,13: SMA(2) = 12.5, SMA(4) = 11.5 -> fast > slow
    sma, _ = SmaCrossover.create({"fast": 2, "slow": 4})
    assert sma.explain(history([10, 11, 12, 13])) == (
        "SMA(2) 12.50 vs SMA(4) 11.50: fast > slow: hold the asset."
    )
    trend, _ = TrendFilter.create({"lookback": 3})
    assert (
        trend.explain(history([10, 11, 9])) == "Close 9.00 vs SMA(3) 10.00: not above: hold cash."
    )
    assert trend.target_exposure(history([10, 11, 12])) == 1.0
    assert "3 needed" in trend.explain(history([10]))


def test_sweep_grid_defaults_to_param_defaults() -> None:
    assert SmaCrossover.grid() == {"fast": [10, 20, 50], "slow": [100, 150, 200]}

    @register
    class Plain(EquityStrategy):
        id = "test_plain"
        name = "Plain"
        description = "test-only"
        params: ClassVar[tuple[ParamSpec, ...]] = (ParamSpec("n", "N", 7, 1, 10, "n"),)

        def __init__(self, n: int) -> None:
            self.n = n

        def target_exposure(self, history: History) -> float:
            return 0.0

    try:
        assert Plain.grid() == {"n": [7]}
        with pytest.raises(ValueError, match="already registered"):

            @register
            class Clash(Plain):
                id = "test_plain"
    finally:
        REGISTRY.pop("test_plain", None)
