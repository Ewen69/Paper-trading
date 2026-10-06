"""Sectors and the agents that live in them. Sectors have spare slots for future sub-agents."""

from dataclasses import dataclass
from typing import Literal

JobKind = Literal["sweep", "data_check", "paper_cycle"]


@dataclass(frozen=True, slots=True)
class Sector:
    id: str
    name: str
    purpose: str
    slots: int  # capacity for agents, including sub-agents added later


@dataclass(frozen=True, slots=True)
class AgentDef:
    id: str
    name: str
    sector: str
    role: str
    kinds: tuple[JobKind, ...]
    asset: Literal["equity", "options"] | None = None


SECTORS: tuple[Sector, ...] = (
    Sector("data", "Data Ingestion Sector", "Imports, quality checks, live feed", 4),
    Sector(
        "research",
        "Strategy Research Sector",
        "In-sample parameter sweeps; at most one counted out-of-sample test per sweep",
        6,
    ),
    Sector("risk", "Options Risk Sector", "Risk limits, kill switch, defined-risk checks", 4),
    Sector("execution", "Execution Hub", "Paper runner: Alpaca paper account or dry run", 4),
)

AGENTS: tuple[AgentDef, ...] = (
    AgentDef(
        "scout", "Data Scout", "data", "Checks data health and the live feed", ("data_check",)
    ),
    AgentDef(
        "quant-equity",
        "Equity Quant",
        "research",
        "Sweeps equity strategies in-sample",
        ("sweep",),
        "equity",
    ),
    AgentDef(
        "quant-options",
        "Options Quant",
        "research",
        "Sweeps options strategies in-sample",
        ("sweep",),
        "options",
    ),
    AgentDef("risk-officer", "Risk Officer", "risk", "Evaluates every paper order", ()),
    AgentDef(
        "paper-trader",
        "Paper Trader",
        "execution",
        "Runs paper cycles (dry run by default)",
        ("paper_cycle",),
    ),
)

AGENT_BY_ID = {a.id: a for a in AGENTS}
