"""Live option chain quotes for the paper runner (Alpaca market data, read-only).

The free plan's "indicative" options feed is delayed and modified from OPRA; it is labeled as
such everywhere it's shown. Quotes are only used to size and price paper orders; spreads are
priced at the bid for what we sell and the ask for what we buy, never at mid.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from ptl.config import Settings
from ptl.options.models import Contract

_OCC = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


@dataclass(frozen=True, slots=True)
class ChainQuote:
    symbol: str  # OCC symbol, e.g. SPY261120P00540000
    contract: Contract
    bid: float
    ask: float
    as_of: datetime | None


def parse_occ(symbol: str, underlying: str) -> Contract:
    m = _OCC.match(symbol.strip().upper())
    if m is None:
        raise ValueError(f"Not an OCC option symbol: {symbol!r}")
    root, ymd, kind, strike = m.groups()
    expiration = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return Contract(
        underlying=underlying,
        root=root,
        expiration=expiration,
        strike=int(strike) / 1000,
        option_type="call" if kind == "C" else "put",
        style="american",  # US equity and ETF options are American-style
    )


def occ_symbol(root: str, expiration: date, option_type: str, strike: float) -> str:
    return (
        f"{root.upper()}{expiration:%y%m%d}{'C' if option_type == 'call' else 'P'}"
        f"{round(strike * 1000):08d}"
    )


class OptionChainSource(Protocol):
    @property
    def label(self) -> str: ...

    def put_chain(self, underlying: str, exp_from: date, exp_to: date) -> list[ChainQuote]: ...

    def quotes(self, underlying: str, symbols: Sequence[str]) -> dict[str, ChainQuote]: ...


def _quote(symbol: str, underlying: str, q: Any) -> ChainQuote | None:  # noqa: ANN401
    if q is None:
        return None
    bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
    return ChainQuote(symbol, parse_occ(symbol, underlying), bid, ask, q.timestamp)


class AlpacaOptionChain:
    def __init__(self, settings: Settings, client: Any = None) -> None:  # noqa: ANN401
        from alpaca.data.historical import OptionHistoricalDataClient  # noqa: PLC0415

        from ptl.data.alpaca_source import _OPTION_FEEDS  # noqa: PLC0415

        self._feed, info = _OPTION_FEEDS[settings.alpaca_options_feed]
        self._label = f"Alpaca options data ({info.name})"
        if client is None:
            if not settings.broker_credentials_configured:
                raise ValueError("Alpaca API keys are not set.")
            key, secret = settings.alpaca_api_key_id, settings.alpaca_api_secret_key
            assert key is not None  # noqa: S101 - checked above
            assert secret is not None  # noqa: S101
            client = OptionHistoricalDataClient(key.get_secret_value(), secret.get_secret_value())
        self._client = client

    @property
    def label(self) -> str:
        return self._label

    def put_chain(self, underlying: str, exp_from: date, exp_to: date) -> list[ChainQuote]:
        from alpaca.data.requests import OptionChainRequest  # noqa: PLC0415
        from alpaca.trading.enums import ContractType  # noqa: PLC0415

        snapshots = self._client.get_option_chain(
            OptionChainRequest(
                underlying_symbol=underlying,
                feed=self._feed,
                type=ContractType.PUT,
                expiration_date_gte=exp_from,
                expiration_date_lte=exp_to,
            )
        )
        out = []
        for symbol, snap in dict(snapshots).items():
            q = _quote(symbol, underlying, getattr(snap, "latest_quote", None))
            if q is not None:
                out.append(q)
        return out

    def quotes(self, underlying: str, symbols: Sequence[str]) -> dict[str, ChainQuote]:
        from alpaca.data.requests import OptionLatestQuoteRequest  # noqa: PLC0415

        if not symbols:
            return {}
        result = self._client.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=list(symbols), feed=self._feed)
        )
        out = {}
        for symbol, q in dict(result).items():
            parsed = _quote(symbol, underlying, q)
            if parsed is not None:
                out[symbol] = parsed
        return out
