"""Static guard: no source file may reference the live trading host or disable paper mode."""

import re
from pathlib import Path

import pytest

from ptl.config import REPO_ROOT

_SOURCE_DIRS = (REPO_ROOT / "backend" / "src", REPO_ROOT / "frontend" / "src")
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
_FORBIDDEN = (
    re.compile(r"(?<!paper-)api\.alpaca\.markets"),  # live trading host
    re.compile(r"paper\s*=\s*False"),
    re.compile(r"\bLIVE_TRADING\b", re.IGNORECASE),
)


def _source_files() -> list[Path]:
    return [
        path
        for root in _SOURCE_DIRS
        if root.exists()
        for path in root.rglob("*")
        if path.suffix in _SOURCE_SUFFIXES and "node_modules" not in path.parts
    ]


def test_source_tree_is_scanned() -> None:
    assert any(p.name == "safety.py" for p in _source_files())


@pytest.mark.parametrize("pattern", _FORBIDDEN, ids=lambda p: p.pattern)
def test_no_live_trading_references(pattern: re.Pattern[str]) -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{lineno}"
        for path in _source_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []
