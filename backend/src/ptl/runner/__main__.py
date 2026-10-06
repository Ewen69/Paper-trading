"""`python -m ptl.runner [--dry-run | --paper] [--once]` - the paper execution daemon.

Dry run is the default. `--paper` sends orders to the Alpaca PAPER account (simulated money)
and needs paper API keys in .env. There is no live-trading option.
"""

import argparse
from datetime import UTC, datetime

from ptl.config import Settings
from ptl.data.alpaca_source import AlpacaQuoteSource
from ptl.paper.factory import broker_factory
from ptl.paper.runner import CycleRefusedError
from ptl.runner.daemon import run_daemon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ptl-runner", description="Paper execution daemon.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="log intended orders only (default)")
    mode.add_argument("--paper", action="store_true", help="send to the Alpaca PAPER account")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        broker = broker_factory(settings)(not args.paper)
    except CycleRefusedError as exc:
        print(f"error: {exc}")  # noqa: T201 - CLI output
        return 2
    quotes = AlpacaQuoteSource(settings, lambda: datetime.now(UTC))
    try:
        run_daemon(settings, broker=broker, quotes=quotes, once=args.once)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
