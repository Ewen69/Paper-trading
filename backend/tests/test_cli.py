from pathlib import Path

import pytest

from ptl.cli import main
from tests.conftest import FIXTURES, CsvWriter


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))


def test_import_list_delete_roundtrip(capsys: pytest.CaptureFixture[str]) -> None:
    csv = str(FIXTURES / "synthetic_equity_bars.csv")
    args = ["import-csv", csv, "--kind", "equity-bars", "--source", "Test vendor"]
    assert main([*args, "--map", "adj_close=Adj Close"]) == 0
    assert "Imported: dataset #1, 6 rows." in capsys.readouterr().out

    assert main([*args, "--map", "adj_close=Adj Close"]) == 0
    assert "Already imported: dataset #1" in capsys.readouterr().out

    assert main(["datasets"]) == 0
    assert "source=Test vendor" in capsys.readouterr().out

    assert main(["delete-dataset", "1"]) == 0
    assert main(["datasets"]) == 0
    assert "No datasets imported yet." in capsys.readouterr().out


def test_rejected_import_exits_nonzero(
    capsys: pytest.CaptureFixture[str], write_csv: CsvWriter
) -> None:
    path = write_csv("bad.csv", "symbol,date\nSPY,2025-01-02")
    code = main(["import-csv", str(path), "--kind", "equity-bars", "--source", "x"])
    assert code == 1
    assert "missing required column" in capsys.readouterr().out


def test_missing_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(
        ["import-csv", str(tmp_path / "nope.csv"), "--kind", "equity-bars", "--source", "x"]
    )
    assert code == 2
    assert "file not found" in capsys.readouterr().out
