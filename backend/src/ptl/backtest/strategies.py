"""Built-in equity strategies, plus the registry view the backtester uses.

Every strategy is a plugin (see ptl/strategy/base.py). STRATEGIES lists the registered equity
strategies, including any found in ptl/strategy/plugins/.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar, Protocol

from ptl.backtest.models import History
from ptl.strategy.base import (
    EquityStrategy,
    ParamSpec,
    equity_strategies,
    load_plugins,
    register,
    resolve_params,
)

__all__ = [
    "STRATEGIES",
    "BuyAndHold",
    "ParamSpec",
    "SmaCrossover",
    "StrategySpec",
    "resolve_params",
]


class Strategy(Protocol):
    """What the backtest engine needs from an equity strategy."""

    @property
    def warmup(self) -> int: ...

    def target_exposure(self, history: History) -> float: ...


@dataclass(frozen=True, slots=True)
class StrategySpec:
    id: str
    name: str
    description: str
    params: tuple[ParamSpec, ...]
    build: Callable[[Mapping[str, int]], Strategy]

    def create(self, params: Mapping[str, int]) -> tuple[Strategy, dict[str, int]]:
        """Validate params (filling defaults) and build the strategy."""
        resolved = resolve_params(self.id, self.params, params)
        return self.build(resolved), resolved


@register
class BuyAndHold(EquityStrategy):
    """Fully invested from the first possible fill to the end. The benchmark."""

    id = "buy_and_hold"
    name = "Buy and hold"
    description = "Buy at the first possible fill and hold to the end (the benchmark)."

    def target_exposure(self, history: History) -> float:
        return 1.0

    def explain(self, history: History) -> str:
        return "Buy and hold: always 100% invested."


def _mean(values: list[float] | tuple[float, ...]) -> float:
    return sum(values) / len(values)


@register
class SmaCrossover(EquityStrategy):
    """Long when the fast simple moving average of closes is above the slow one; else cash."""

    id = "sma_crossover"
    name = "Moving-average crossover"
    description = (
        "Hold the asset while the fast moving average of closing prices is above the slow "
        "one; otherwise hold cash (cash earns 0% here). Signals use closes up to the decision "
        "day and fill at the next session's open."
    )
    params: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("fast", "Fast window", 50, 2, 399, "Sessions in the fast average."),
        ParamSpec("slow", "Slow window", 200, 3, 400, "Sessions in the slow average."),
    )
    sweep_grid: ClassVar[dict[str, list[int]]] = {"fast": [10, 20, 50], "slow": [100, 150, 200]}

    def __init__(self, fast: int, slow: int) -> None:
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self.fast = fast
        self.slow = slow

    @property
    def warmup(self) -> int:
        return self.slow

    def _averages(self, history: History) -> tuple[float, float] | None:
        if len(history) < self.slow:
            return None
        closes = list(history.closes(self.slow))
        return _mean(closes[-self.fast :]), _mean(closes)

    def target_exposure(self, history: History) -> float:
        averages = self._averages(history)
        return 1.0 if averages is not None and averages[0] > averages[1] else 0.0

    def explain(self, history: History) -> str:
        averages = self._averages(history)
        if averages is None:
            return f"Only {len(history)} sessions of history; {self.slow} needed: hold cash."
        fast, slow = averages
        verdict = "fast > slow: hold the asset" if fast > slow else "fast <= slow: hold cash"
        return f"SMA({self.fast}) {fast:,.2f} vs SMA({self.slow}) {slow:,.2f}: {verdict}."


def _spec(cls: type[EquityStrategy]) -> StrategySpec:
    def build(params: Mapping[str, int]) -> Strategy:
        return cls(**params)

    return StrategySpec(cls.id, cls.name, cls.description, cls.params, build)


load_plugins()
STRATEGIES: dict[str, StrategySpec] = {sid: _spec(c) for sid, c in equity_strategies().items()}
