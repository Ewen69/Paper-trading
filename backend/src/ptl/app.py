"""FastAPI application factory.

Run with: `uvicorn ptl.app:create_app --factory`. The paper-only assertion runs before the app
object exists, so a misconfigured endpoint means the server never starts.
"""

import logging

from fastapi import FastAPI

from ptl import __version__
from ptl.config import Settings
from ptl.health import build_health_router
from ptl.safety import assert_paper_endpoint

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    paper_base_url = assert_paper_endpoint(settings.alpaca_base_url)
    logger.info(
        "Paper-only check passed (broker=%s, credentials configured=%s)",
        paper_base_url,
        settings.broker_credentials_configured,
    )

    app = FastAPI(title="Paper Trading Lab", version=__version__)
    app.include_router(build_health_router(settings, paper_base_url))
    return app
