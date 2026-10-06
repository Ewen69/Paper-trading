"""Built-in strategies. A strategy maps the visible history to a target exposure in [0, 1].

Strategies are stateless: the same history always gives the same answer, so a strategy has
nowhere to hide future information. Phase 3 turns this into a plugin interface.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ptl.backtest.models import History


class Strategy(Protocol):
    @property
    def warmup(self) -> int:
        """Sessions of history needed before the first real signal."""
        ...

    def target_exposure(self, history: History) -> float: ...


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    label: str
    default: int
    minimum: int
    maximum: int
    description: str


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


def resolve_params(
    strategy_id: str, specs: tuple[ParamSpec, ...], params: Mapping[str, int]
) -> dict[str, int]:
    """Check names, types and ranges; fill defaults. Shared by equity and options strategies."""
    unknown = set(params) - {p.name for p in specs}
    if unknown:
        raise ValueError(f"unknown parameter(s) for {strategy_id}: {sorted(unknown)}")
    resolved: dict[str, int] = {}
    for spec in specs:
        value = params.get(spec.name, spec.default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{spec.name} must be a whole number")
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{spec.name} must be between {spec.minimum} and {spec.maximum}")
        resolved[spec.name] = value
    return resolved


class BuyAndHold:
    """Fully invested from the first possible fill to the end. The benchmark."""

    warmup = 0

    def target_exposure(self, history: History) -> float:
        return 1.0


def _mean(values: list[float] | tuple[float, ...]) -> float:
    return sum(values) / len(values)


class SmaCrossover:
    """Long when the fast simple moving average of closes is above the slow one; else cash."""

    def __init__(self, fast: int, slow: int) -> None:
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self.fast = fast
        self.slow = slow

    @property
    def warmup(self) -> int:
        return self.slow

    def target_exposure(self, history: History) -> float:
        if len(history) < self.slow:
            return 0.0
        closes = list(history.closes(self.slow))
        return 1.0 if _mean(closes[-self.fast :]) > _mean(closes) else 0.0


def _sma_crossover(params: Mapping[str, int]) -> Strategy:
    return SmaCrossover(params["fast"], params["slow"])


STRATEGIES: dict[str, StrategySpec] = {
    spec.id: spec
    for spec in (
        StrategySpec(
            id="buy_and_hold",
            name="Buy and hold",
            description="Buy at the first possible fill and hold to the end (the benchmark).",
            params=(),
            build=lambda _params: BuyAndHold(),
        ),
        StrategySpec(
            id="sma_crossover",
            name="Moving-average crossover",
            description=(
                "Hold the asset while the fast moving average of closing prices is above the "
                "slow one; otherwise hold cash (cash earns 0% here). Signals use closes up to "
                "the decision day and fill at the next session's open."
            ),
            params=(
                ParamSpec("fast", "Fast window", 50, 2, 399, "Sessions in the fast average."),
                ParamSpec("slow", "Slow window", 200, 3, 400, "Sessions in the slow average."),
            ),
            build=_sma_crossover,
        ),
    )
}
