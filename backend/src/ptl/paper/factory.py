"""Builds the broker for a paper cycle. Dry run is the default everywhere."""

from collections.abc import Callable

from ptl.config import Settings
from ptl.paper.broker import AlpacaPaperBroker, Broker, DryRunBroker
from ptl.paper.runner import CycleRefusedError


def broker_factory(settings: Settings) -> Callable[[bool], Broker]:
    def make(dry_run: bool) -> Broker:
        if dry_run:
            reader = AlpacaPaperBroker(settings) if settings.broker_credentials_configured else None
            return DryRunBroker(reader, settings.paper_dry_run_capital)
        if not settings.broker_credentials_configured:
            raise CycleRefusedError(
                "Paper mode needs Alpaca paper API keys in .env. Use dry run until they're set."
            )
        return AlpacaPaperBroker(settings)

    return make
