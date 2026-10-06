import pytest

from ptl.safety import ALPACA_PAPER_BASE_URL, LiveTradingRefusedError, assert_paper_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "https://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets/",
        "https://paper-api.alpaca.markets/v2",
        "https://paper-api.alpaca.markets/v2/",
        "https://PAPER-API.alpaca.markets",
        "https://paper-api.alpaca.markets:443",
        "  https://paper-api.alpaca.markets  ",
    ],
)
def test_accepts_paper_endpoint(url: str) -> None:
    assert assert_paper_endpoint(url) == ALPACA_PAPER_BASE_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://api.alpaca.markets",  # live trading host
        "https://api.alpaca.markets/v2",
        "http://paper-api.alpaca.markets",  # not TLS
        "https://paper-api.alpaca.markets.evil.example",  # lookalike suffix
        "https://evil.example/paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets@api.alpaca.markets",  # userinfo trick
        "https://user:pass@paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets:8443",
        "https://paper-api.alpaca.markets:notaport",
        "https://paper-api.alpaca.markets/v2/orders",
        "https://paper-api.alpaca.markets?live=1",
        "https://paper-api.alpaca.markets#x",
        "https://data.alpaca.markets",
        "paper-api.alpaca.markets",  # no scheme
        "",
    ],
)
def test_refuses_anything_else(url: str) -> None:
    with pytest.raises(LiveTradingRefusedError, match="Refusing to run"):
        assert_paper_endpoint(url)
