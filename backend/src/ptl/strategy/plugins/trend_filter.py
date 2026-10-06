"""Example plugin: a single-moving-average trend filter. Copy this file to add a strategy."""

from typing import ClassVar

from ptl.backtest.models import History
from ptl.strategy.base import EquityStrategy, ParamSpec, register


@register
class TrendFilter(EquityStrategy):
    id = "trend_filter"
    name = "Trend filter"
    description = (
        "Hold the asset while the latest close is above its simple moving average; otherwise "
        "hold cash. Signals use closes up to the decision day."
    )
    params: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("lookback", "Average length", 200, 2, 400, "Sessions in the moving average."),
    )
    sweep_grid: ClassVar[dict[str, list[int]]] = {"lookback": [50, 100, 150, 200]}
    search_space: ClassVar[dict[str, tuple[int, int]]] = {"lookback": (20, 300)}

    def __init__(self, lookback: int) -> None:
        self.lookback = lookback

    @property
    def warmup(self) -> int:
        return self.lookback

    def _average(self, history: History) -> float | None:
        if len(history) < self.lookback:
            return None
        closes = history.closes(self.lookback)
        return sum(closes) / len(closes)

    def target_exposure(self, history: History) -> float:
        average = self._average(history)
        return 1.0 if average is not None and history.current.close > average else 0.0

    def explain(self, history: History) -> str:
        average = self._average(history)
        close = history.current.close
        if average is None:
            return f"Only {len(history)} sessions of history; {self.lookback} needed: hold cash."
        verdict = "above: hold the asset" if close > average else "not above: hold cash"
        return f"Close {close:,.2f} vs SMA({self.lookback}) {average:,.2f}: {verdict}."
