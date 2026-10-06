"""`/health` endpoint: liveness plus the paper-only configuration the UI must display."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ptl import __version__
from ptl.config import Settings


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    trading_mode: Literal["paper"]
    broker_base_url: str
    broker_credentials_configured: bool
    source: Literal["backend server clock"]
    as_of: datetime


def build_health_router(settings: Settings, paper_base_url: str) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            trading_mode="paper",
            broker_base_url=paper_base_url,
            broker_credentials_configured=settings.broker_credentials_configured,
            source="backend server clock",
            as_of=datetime.now(UTC),
        )

    return router
