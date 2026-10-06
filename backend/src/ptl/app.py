"""FastAPI application factory.

Run with: `uvicorn ptl.app:create_app --factory`. The paper-only assertion runs before the app
object exists, so a misconfigured endpoint means the server never starts.
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI

from ptl import __version__
from ptl.backtest_api import build_backtest_router
from ptl.config import Settings
from ptl.data.alpaca_source import AlpacaQuoteSource
from ptl.data.live import Clock, QuoteSource
from ptl.data_api import build_data_router
from ptl.db import migrate, open_db
from ptl.health import build_health_router
from ptl.market_calendar import MarketCalendar
from ptl.safety import assert_paper_endpoint

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")


def create_app(
    settings: Settings | None = None,
    *,
    quote_source: QuoteSource | None = None,
    calendar: MarketCalendar | None = None,
    clock: Clock = utc_now,
) -> FastAPI:
    _configure_logging()
    settings = settings if settings is not None else Settings()
    paper_base_url = assert_paper_endpoint(settings.alpaca_base_url)
    logger.info(
        "Paper-only check passed (broker=%s, credentials configured=%s)",
        paper_base_url,
        settings.broker_credentials_configured,
    )

    with open_db(settings.database_path) as conn:
        version = migrate(conn)
    logger.info("Database ready at %s (schema v%d)", settings.database_path, version)

    calendar = calendar if calendar is not None else MarketCalendar()
    app = FastAPI(title="Paper Trading Lab", version=__version__)
    app.include_router(build_health_router(settings, paper_base_url))
    app.include_router(
        build_data_router(
            settings,
            quote_source if quote_source is not None else AlpacaQuoteSource(settings, clock),
            calendar,
            clock,
        )
    )
    app.include_router(build_backtest_router(settings, calendar, clock))
    return app
