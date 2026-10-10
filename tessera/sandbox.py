"""Where generated code and tests run.

Two runners behind one interface:
  * LocalRunner: subprocess with a timeout. For development only.
  * Token Factory Sandboxes (contree-sdk): the real target. Isolated, snapshot-able
    environments, built for agents that write, run, and test code. See the TODO below.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run_pytest(self, workdir: Path, timeout_s: int = 60) -> RunResult: ...


class LocalRunner:
    """Dev-only. Do NOT point this at untrusted generated code in the hosted demo."""

    def run_pytest(self, workdir: Path, timeout_s: int = 60) -> RunResult:
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", str(workdir)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(124, e.stdout or "", f"timeout after {timeout_s}s")
        return RunResult(p.returncode, p.stdout, p.stderr)


# TODO(day 3-4): ContreeRunner using `contree-sdk` (docs: sandboxes/start/python-quickstart).
# Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID. Pattern from the docs:
#   client = Contree(); base = await client.images.use("python:3.12", strict=True)
#   result = await base.run(shell="pytest -q", disposable=True)  # exit_code / stdout / stderr
# Prepare an image with the scaffold + deps once (disposable=False), then branch per attempt.
