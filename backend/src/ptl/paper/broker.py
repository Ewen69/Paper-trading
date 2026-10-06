"""Broker adapters for the paper runner: Alpaca PAPER, and dry-run.

The Alpaca adapter can only be built with `make_paper_trading_client`, and it re-checks the
paper endpoint immediately before every order submission. There is no live adapter.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import uuid4

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
    OrderRequest,
)

from ptl.config import Settings
from ptl.data.alpaca_source import make_paper_trading_client
from ptl.safety import assert_paper_endpoint

Mode = Literal["dry_run", "paper"]
Side = Literal["buy", "sell"]
FINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected", "not_sent"})


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    equity: float
    last_equity: float  # equity at the previous close
    cash: float
    source: str


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    qty: float
    market_value: float


@dataclass(frozen=True, slots=True)
class OrderTicket:
    symbol: str
    side: Side
    qty: float


@dataclass(frozen=True, slots=True)
class SpreadLeg:
    symbol: str  # OCC option symbol
    side: Side  # sell = short leg, buy = long leg


@dataclass(frozen=True, slots=True)
class SpreadTicket:
    """A defined-risk vertical as ONE multi-leg order: both legs fill together or not at all.

    `net_limit` is per share: negative = a credit we must receive at least, positive = a debit
    we will pay at most (Alpaca's mleg convention). Quantity is spreads (contracts per leg).
    """

    legs: tuple[SpreadLeg, SpreadLeg]
    contracts: int
    net_limit: float
    opening: bool


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    id: str
    status: str
    filled_qty: float | None
    fill_price: float | None


class Broker(Protocol):
    @property
    def mode(self) -> Mode: ...
    def account(self) -> BrokerAccount: ...
    def positions(self) -> dict[str, BrokerPosition]: ...
    def submit(self, ticket: OrderTicket) -> BrokerOrder: ...
    def submit_spread(self, ticket: SpreadTicket) -> BrokerOrder: ...
    def order_status(self, order_id: str) -> BrokerOrder: ...


class TradingClientLike(Protocol):
    def get_account(self) -> TradeAccount | dict[str, Any]: ...
    def get_all_positions(self) -> list[Position] | dict[str, Any]: ...
    def submit_order(self, order_data: OrderRequest) -> Order | dict[str, Any]: ...
    def get_order_by_id(self, order_id: Any, filter: Any = None) -> Order | dict[str, Any]: ...  # noqa: ANN401


def _num(value: object) -> float | None:
    return None if value is None else float(str(value))


def _to_order(order: Order | dict[str, Any]) -> BrokerOrder:
    if not isinstance(order, Order):
        raise TypeError("unexpected order response from Alpaca")
    status = order.status.value if isinstance(order.status, Enum) else str(order.status)
    return BrokerOrder(str(order.id), status, _num(order.filled_qty), _num(order.filled_avg_price))


class AlpacaPaperBroker:
    """Alpaca PAPER account. Every submit re-verifies the endpoint first."""

    def __init__(self, settings: Settings, client: TradingClientLike | None = None) -> None:
        self._settings = settings
        self._client: TradingClientLike = client or make_paper_trading_client(settings)
        self._guard()

    @property
    def mode(self) -> Mode:
        return "paper"

    def _guard(self) -> None:
        assert_paper_endpoint(self._settings.alpaca_base_url)
        base: object = getattr(self._client, "_base_url", None)
        assert_paper_endpoint(str(base.value if isinstance(base, Enum) else base))

    def account(self) -> BrokerAccount:
        acct = self._client.get_account()
        if not isinstance(acct, TradeAccount):
            raise TypeError("unexpected account response from Alpaca")
        return BrokerAccount(
            equity=_num(acct.equity) or 0.0,
            last_equity=_num(acct.last_equity) or 0.0,
            cash=_num(acct.cash) or 0.0,
            source="Alpaca paper account",
        )

    def positions(self) -> dict[str, BrokerPosition]:
        rows = self._client.get_all_positions()
        if not isinstance(rows, list):
            raise TypeError("unexpected positions response from Alpaca")
        return {
            p.symbol: BrokerPosition(p.symbol, _num(p.qty) or 0.0, _num(p.market_value) or 0.0)
            for p in rows
        }

    def submit(self, ticket: OrderTicket) -> BrokerOrder:
        self._guard()  # immediately before the order leaves this process
        request = MarketOrderRequest(
            symbol=ticket.symbol,
            qty=ticket.qty,
            side=OrderSide.BUY if ticket.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=f"ptl-{uuid4().hex[:20]}",
        )
        return _to_order(self._client.submit_order(order_data=request))

    def submit_spread(self, ticket: SpreadTicket) -> BrokerOrder:
        self._guard()  # immediately before the order leaves this process
        if {leg.side for leg in ticket.legs} != {"buy", "sell"}:
            raise ValueError("A vertical needs one long and one short leg (no naked shorts).")

        def intent(side: str) -> PositionIntent:
            if ticket.opening:
                return PositionIntent.SELL_TO_OPEN if side == "sell" else PositionIntent.BUY_TO_OPEN
            return PositionIntent.SELL_TO_CLOSE if side == "sell" else PositionIntent.BUY_TO_CLOSE

        request = LimitOrderRequest(
            qty=ticket.contracts,
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            limit_price=round(ticket.net_limit, 2),
            legs=[
                OptionLegRequest(
                    symbol=leg.symbol,
                    ratio_qty=1,
                    side=OrderSide.SELL if leg.side == "sell" else OrderSide.BUY,
                    position_intent=intent(leg.side),
                )
                for leg in ticket.legs
            ],
            client_order_id=f"ptl-{uuid4().hex[:20]}",
        )
        return _to_order(self._client.submit_order(order_data=request))

    def order_status(self, order_id: str) -> BrokerOrder:
        return _to_order(self._client.get_order_by_id(order_id))


class DryRunBroker:
    """Never sends anything. Reads the paper account when keys exist, else a stated starting
    balance, and records what it would have done."""

    def __init__(self, reader: AlpacaPaperBroker | None, starting_capital: float) -> None:
        self._reader = reader
        self._capital = starting_capital
        self._counter = 0

    @property
    def mode(self) -> Mode:
        return "dry_run"

    def account(self) -> BrokerAccount:
        if self._reader is not None:
            acct = self._reader.account()
            return BrokerAccount(
                acct.equity, acct.last_equity, acct.cash, acct.source + " (read-only)"
            )
        return BrokerAccount(
            self._capital,
            self._capital,
            self._capital,
            f"Dry run: stated starting capital ${self._capital:,.0f} (no broker connected)",
        )

    def positions(self) -> dict[str, BrokerPosition]:
        return self._reader.positions() if self._reader is not None else {}

    def submit(self, ticket: OrderTicket) -> BrokerOrder:
        self._counter += 1
        return BrokerOrder(f"dry-{self._counter}", "not_sent", None, None)

    def submit_spread(self, ticket: SpreadTicket) -> BrokerOrder:
        self._counter += 1
        return BrokerOrder(f"dry-{self._counter}", "not_sent", None, None)

    def order_status(self, order_id: str) -> BrokerOrder:
        return BrokerOrder(order_id, "not_sent", None, None)
