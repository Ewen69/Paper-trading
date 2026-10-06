"""One-command launcher: `npm run start:all` (= `python -m ptl.orchestrator`).

1. Verifies Python, the backend packages, Node/npm and the frontend install.
2. Checks the broker URL is the PAPER endpoint and applies database migrations.
3. Starts, concurrently, with prefixed output:
   - api        FastAPI backend on 127.0.0.1:8000 (telemetry WebSocket included)
   - optimizer  the learning optimizer daemon (in-sample search)
   - runner     the paper execution daemon (DRY RUN unless --paper)
   - web        the Vite dev server on http://localhost:5173
Ctrl+C (or any process exiting) stops everything.
"""

import argparse
import importlib.util
import io
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ptl.config import Settings
from ptl.db import migrate, open_db
from ptl.safety import assert_paper_endpoint

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
REQUIRED_MODULES = ("fastapi", "uvicorn", "alpaca", "numpy", "exchange_calendars", "pydantic")


@dataclass
class Child:
    name: str
    process: subprocess.Popen[str]


def _say(name: str, line: str) -> None:
    text = f"[{name:9}] {line.rstrip()}\n"
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:  # a console that can't show a character must not stop the pump
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
    sys.stdout.flush()


def check_environment() -> list[str]:
    """Return problems found; empty means ready."""
    problems = []
    if sys.version_info < (3, 12):  # noqa: UP036 - explicit message for older interpreters
        problems.append(f"Python 3.12+ required, found {sys.version.split()[0]}.")
    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        problems.append(f"Missing Python packages {missing}: run `npm install` (it runs uv sync).")
    if shutil.which("node") is None or shutil.which("npm") is None:
        problems.append("Node.js and npm must be on PATH (https://nodejs.org).")
    if not (FRONTEND / "node_modules").is_dir():
        problems.append("Frontend packages not installed: run `npm install` at the repo root.")
    return problems


def _spawn(name: str, args: list[str], cwd: Path) -> Child:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(  # noqa: S603 - fixed argument lists, no shell
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        start_new_session=os.name != "nt",
    )

    def pump() -> None:
        assert process.stdout is not None  # noqa: S101 - set by PIPE above
        for line in process.stdout:
            _say(name, line)

    threading.Thread(target=pump, daemon=True).start()
    return Child(name, process)


def _stop(child: Child) -> None:
    if child.process.poll() is not None:
        return
    if sys.platform == "win32":  # kill the whole tree (npm -> node, uvicorn workers)
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(child.process.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        os.killpg(child.process.pid, signal.SIGTERM)


def commands(
    *, paper: bool, optimizer: bool, runner: bool, web: bool
) -> list[tuple[str, list[str], Path]]:
    py = sys.executable
    npm = shutil.which("npm") or "npm"
    out = [
        (
            "api",
            [
                py,
                "-m",
                "uvicorn",
                "ptl.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            BACKEND,
        )
    ]
    if optimizer:
        out.append(("optimizer", [py, "-m", "ptl.agents.optimizer"], BACKEND))
    if runner:
        out.append(
            ("runner", [py, "-m", "ptl.runner", "--paper" if paper else "--dry-run"], BACKEND)
        )
    if web:
        out.append(("web", [npm, "run", "dev"], FRONTEND))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ptl-orchestrator", description="Start everything.")
    parser.add_argument(
        "--paper", action="store_true", help="runner sends to the Alpaca PAPER account"
    )
    parser.add_argument("--no-optimizer", action="store_true")
    parser.add_argument("--no-runner", action="store_true")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--check", action="store_true", help="verify and migrate, then exit")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    problems = check_environment()
    if problems:
        for p in problems:
            _say("check", f"FAIL {p}")
        return 1
    settings = Settings()
    assert_paper_endpoint(settings.alpaca_base_url)
    with open_db(settings.database_path) as conn:
        version = migrate(conn)
    _say("check", f"OK Python {sys.version.split()[0]}, Node/npm found, paper endpoint verified.")
    _say("check", f"OK database {settings.database_path} at schema v{version}.")
    if args.check:
        return 0

    children = [
        _spawn(name, cmd, cwd)
        for name, cmd, cwd in commands(
            paper=args.paper,
            optimizer=not args.no_optimizer,
            runner=not args.no_runner,
            web=not args.no_web,
        )
    ]
    _say("launcher", "Operations Center: http://localhost:5173  (Ctrl+C stops everything)")
    if not args.paper and not args.no_runner:
        _say("launcher", "Paper runner is in DRY RUN: it logs intended orders and sends nothing.")
    code = 0
    try:
        while True:
            for child in children:
                rc = child.process.poll()
                if rc is not None:
                    _say("launcher", f"{child.name} exited with code {rc}; stopping the rest.")
                    code = rc or 1
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            _stop(child)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
