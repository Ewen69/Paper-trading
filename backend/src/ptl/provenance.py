"""Provenance attached to every value the UI displays: where it came from, when, and what kind."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict


class DataType(StrEnum):
    REAL_TIME = "real-time"
    DELAYED = "delayed"
    END_OF_DAY = "end-of-day"
    MANUAL = "manual"


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    as_of: AwareDatetime
    data_type: DataType
    stale: bool
    stale_reason: str | None = None
