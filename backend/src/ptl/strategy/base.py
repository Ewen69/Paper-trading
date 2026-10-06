"""Strategy plugin interface.

A strategy is a class that inherits `EquityStrategy` or `OptionStrategy`, declares its id,
name, description and integer parameters, and is decorated with `@register`. Put new ones in
`ptl/strategy/plugins/`; every module there is imported at startup, so a new file is all it
takes. Strategies never place orders themselves. Engines (the backtester, the paper runner)
ask them what they want and decide how to execute.

Contract for every strategy:
- Stateless: the same inputs always give the same answer, so a strategy has nowhere to hide
  future information.
- `explain()` returns the exact arithmetic behind a decision; the paper runner logs it.
"""

import importlib
import pkgutil
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal, Self

from ptl.backtest.models import History
from ptl.options.engine import Intent, OptionsView

Asset = Literal["equity", "options"]


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    label: str
    default: int
    minimum: int
    maximum: int
    description: str


def resolve_params(
    strategy_id: str, specs: tuple[ParamSpec, ...], params: Mapping[str, int]
) -> dict[str, int]:
    """Check names, types and ranges; fill defaults."""
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


class Strategy(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    asset: ClassVar[Asset]
    params: ClassVar[tuple[ParamSpec, ...]] = ()
    # Values the research agents sweep in-sample. Defaults to each parameter's default.
    sweep_grid: ClassVar[Mapping[str, Sequence[int]]] = {}
    # Inclusive ranges the learning optimizer may explore; params not listed stay at default.
    search_space: ClassVar[Mapping[str, tuple[int, int]]] = {}

    @property
    def warmup(self) -> int:
        """Sessions of history needed before the first real signal."""
        return 0

    @classmethod
    def create(cls, params: Mapping[str, int]) -> tuple[Self, dict[str, int]]:
        """Validate params (filling defaults) and build an instance. Raises ValueError."""
        resolved = resolve_params(cls.id, cls.params, params)
        return cls(**resolved), resolved

    @classmethod
    def space(cls) -> dict[str, tuple[int, int]]:
        """Search ranges for the optimizer, clipped to each parameter's allowed bounds."""
        out = {}
        for p in cls.params:
            if p.name in cls.search_space:
                lo, hi = cls.search_space[p.name]
                out[p.name] = (max(lo, p.minimum), min(hi, p.maximum))
        return out

    @classmethod
    def grid(cls) -> dict[str, list[int]]:
        return {p.name: list(cls.sweep_grid.get(p.name, [p.default])) for p in cls.params}


class EquityStrategy(Strategy):
    """Maps visible history to a target exposure in [0, 1] (long only, no leverage)."""

    asset: ClassVar[Asset] = "equity"

    @abstractmethod
    def target_exposure(self, history: History) -> float: ...

    def explain(self, history: History) -> str:
        return f"{self.name}: target exposure {self.target_exposure(history):.0%}."


class OptionStrategy(Strategy):
    """Looks at today's chain and returns defined-risk orders for the next session."""

    asset: ClassVar[Asset] = "options"

    @abstractmethod
    def decide(self, view: OptionsView) -> Sequence[Intent]: ...


REGISTRY: dict[str, type[Strategy]] = {}


def register[S: type[Strategy]](cls: S) -> S:
    """Class decorator: make a strategy available to the backtester, agents and paper runner."""
    if cls.id in REGISTRY and REGISTRY[cls.id] is not cls:
        raise ValueError(f"strategy id {cls.id!r} is already registered")
    REGISTRY[cls.id] = cls
    return cls


def load_plugins() -> None:
    """Import every module in ptl.strategy.plugins so their @register decorators run."""
    package = importlib.import_module("ptl.strategy.plugins")
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module.name}")


def equity_strategies() -> dict[str, type[EquityStrategy]]:
    return {k: v for k, v in REGISTRY.items() if issubclass(v, EquityStrategy)}


def option_strategies() -> dict[str, type[OptionStrategy]]:
    return {k: v for k, v in REGISTRY.items() if issubclass(v, OptionStrategy)}
