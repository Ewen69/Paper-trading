"""Built-in options strategies. Each decision carries the exact arithmetic that triggered it."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from ptl.options.engine import CloseIntent, Intent, OpenIntent, OptionsStrategy, OptionsView
from ptl.options.models import Leg, Quote
from ptl.strategy.base import (
    OptionStrategy,
    ParamSpec,
    load_plugins,
    option_strategies,
    register,
    resolve_params,
)

_STRIKE_TOLERANCE = 1e-9


@register
class PutCreditSpread(OptionStrategy):
    """Sell an out-of-the-money put vertical; take profit early or hold to expiration.

    Open (when fewer than `max_open` positions are open):
      expiration = the first one at least `dte` calendar days away
      short strike = the highest put strike <= close x (1 - otm_percent/100) with a bid
      long strike = short strike - width (must be quoted)
    Close: when the credit captured (credit - cost to close at bid/ask) reaches
    `take_profit` % of the credit. 0 means hold to expiration.
    """

    id = "put_credit_spread"
    name = "Put credit spread"
    description = (
        "Sell an out-of-the-money put vertical (defined risk). Short strike sits a fixed percent "
        "below the close; the long strike is a fixed width lower. Take profit at a share of the "
        "credit, or hold to expiration. Orders fill at the next session's bid/ask."
    )
    params: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("dte", "Min days to expiration", 30, 1, 120, "Calendar days."),
        ParamSpec("otm_percent", "Short strike % below close", 5, 0, 30, "Percent."),
        ParamSpec("width", "Spread width ($)", 5, 1, 100, "Strike distance."),
        ParamSpec("contracts", "Contracts", 1, 1, 50, "Spreads per entry."),
        ParamSpec("take_profit", "Take profit (% of credit)", 50, 0, 100, "0 = hold."),
        ParamSpec("max_open", "Max open spreads", 1, 1, 10, "At a time."),
    )
    search_space: ClassVar[dict[str, tuple[int, int]]] = {
        "dte": (14, 60),
        "otm_percent": (1, 15),
        "width": (1, 20),
        "take_profit": (0, 90),
    }
    sweep_grid: ClassVar[dict[str, list[int]]] = {
        "dte": [30, 45],
        "otm_percent": [3, 5, 8],
        "take_profit": [0, 50],
    }

    def __init__(  # noqa: PLR0913
        self,
        *,
        dte: int,
        otm_percent: int,
        width: int,
        contracts: int,
        take_profit: int,
        max_open: int,
    ) -> None:
        self.dte = dte
        self.otm_percent = otm_percent
        self.width = width
        self.contracts = contracts
        self.take_profit = take_profit
        self.max_open = max_open

    def _take_profits(self, view: OptionsView) -> list[Intent]:
        out: list[Intent] = []
        if not self.take_profit:
            return out
        for p in view.positions:
            if p.close_value is None or p.entry_premium <= 0:
                continue
            captured = p.entry_premium + p.close_value
            target = p.entry_premium * self.take_profit / 100
            if captured >= target:
                out.append(
                    CloseIntent(
                        p.id,
                        f"Take profit: captured {captured:,.2f} = credit {p.entry_premium:,.2f} "
                        f"- cost to close {-p.close_value:,.2f}, which is >= {self.take_profit}% "
                        f"of the credit ({target:,.2f}).",
                    )
                )
        return out

    def decide(self, view: OptionsView) -> Sequence[Intent]:
        intents = self._take_profits(view)
        if len(view.positions) - len(intents) >= self.max_open:
            return intents
        spot = view.history.current.close
        puts = [q for q in view.chain.values() if q.contract.option_type == "put"]
        expirations = sorted(
            {
                q.contract.expiration
                for q in puts
                if (q.contract.expiration - view.day).days >= self.dte
            }
        )
        if not expirations:
            return intents
        expiry = expirations[0]
        limit = spot * (1 - self.otm_percent / 100)
        by_strike: dict[tuple[str, float], Quote] = {
            (q.contract.root, q.contract.strike): q for q in puts if q.contract.expiration == expiry
        }
        shorts = sorted(
            (q for q in by_strike.values() if q.contract.strike <= limit and 0 < q.bid <= q.ask),
            key=lambda q: q.contract.strike,
            reverse=True,
        )
        for short in shorts:
            long_strike = short.contract.strike - self.width
            long = next(
                (
                    q
                    for (root, strike), q in by_strike.items()
                    if root == short.contract.root and abs(strike - long_strike) < _STRIKE_TOLERANCE
                ),
                None,
            )
            if long is None or long.ask <= 0 or long.bid > long.ask:
                continue
            days = (expiry - view.day).days
            reason = (
                f"Open: {expiry} is the first expiration >= {self.dte} days out ({days} days). "
                f"Short {short.contract.strike:g}P is the highest put strike <= close "
                f"{spot:,.2f} x (1 - {self.otm_percent}%) = {limit:,.2f}. Long "
                f"{long.contract.strike:g}P = short - width {self.width}."
            )
            legs = (Leg(short.contract, -1), Leg(long.contract, 1))
            intents.append(OpenIntent(legs, self.contracts, reason))
            break
        return intents


@dataclass(frozen=True, slots=True)
class OptionsStrategySpec:
    id: str
    name: str
    description: str
    params: tuple[ParamSpec, ...]
    build: Callable[[Mapping[str, int]], OptionsStrategy]

    def create(self, params: Mapping[str, int]) -> tuple[OptionsStrategy, dict[str, int]]:
        resolved = resolve_params(self.id, self.params, params)
        return self.build(resolved), resolved


def _spec(cls: type[OptionStrategy]) -> OptionsStrategySpec:
    def build(params: Mapping[str, int]) -> OptionsStrategy:
        return cls(**params)

    return OptionsStrategySpec(cls.id, cls.name, cls.description, cls.params, build)


load_plugins()
OPTIONS_STRATEGIES: dict[str, OptionsStrategySpec] = {
    sid: _spec(c) for sid, c in option_strategies().items()
}
