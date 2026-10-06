"""Paper-only guard.

This is the single place that decides whether a broker endpoint is acceptable. It is called at
app startup and must also be called by any broker adapter immediately before sending an order.
There is deliberately no switch, flag, or override that allows a non-paper endpoint.
"""

from urllib.parse import urlsplit

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_PAPER_HOST = "paper-api.alpaca.markets"
_ALLOWED_PATHS = frozenset({"", "/v2"})
_HTTPS_PORT = 443


class LiveTradingRefusedError(RuntimeError):
    """Raised when the configured broker endpoint is not the Alpaca paper endpoint."""


def assert_paper_endpoint(base_url: str) -> str:
    """Return the canonical paper base URL, or raise if `base_url` is anything else.

    Accepts only https://paper-api.alpaca.markets (optionally with a trailing slash or the
    `/v2` API prefix). Everything else, including the live host, lookalike hosts, plain HTTP,
    credentials embedded in the URL, non-standard ports, queries and fragments, is refused.
    """
    try:
        parts = urlsplit(base_url.strip())
        port = parts.port
    except ValueError as exc:
        raise LiveTradingRefusedError(_refusal(base_url, f"unparseable URL ({exc})")) from exc

    problems: list[str] = []
    if parts.scheme != "https":
        problems.append(f"scheme must be https, got {parts.scheme or 'none'!r}")
    if parts.hostname != ALPACA_PAPER_HOST:
        problems.append(f"host must be {ALPACA_PAPER_HOST!r}, got {parts.hostname!r}")
    if parts.username is not None or parts.password is not None:
        problems.append("URL must not embed credentials")
    if port not in (None, _HTTPS_PORT):
        problems.append(f"port must be default/443, got {port}")
    if parts.path.rstrip("/") not in _ALLOWED_PATHS:
        problems.append(f"unexpected path {parts.path!r}")
    if parts.query or parts.fragment:
        problems.append("URL must not contain a query or fragment")

    if problems:
        raise LiveTradingRefusedError(_refusal(base_url, "; ".join(problems)))
    return ALPACA_PAPER_BASE_URL


def _refusal(base_url: str, reason: str) -> str:
    return (
        f"Refusing to run: broker base URL {base_url!r} is not the Alpaca paper endpoint "
        f"({ALPACA_PAPER_BASE_URL}). {reason}. Paper Trading Lab never trades real money."
    )
