"""Workspaces (a project's copy of the scaffold) and runners (where tests execute).

* Workspace: copies scaffold/ to workspaces/<project>/ and writes agent files into it, but only
  to paths the writing role is allowed to touch.
* LocalRunner: subprocess pytest with a timeout, for development only.
* Token Factory Sandboxes (contree-sdk): the real target, see the TODO at the bottom.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tessera.scaffold_spec import FILE_PATTERNS

SCAFFOLD_DIR = Path(__file__).resolve().parent.parent / "scaffold"
TEST_PATTERNS = [r"tests/test_[a-z0-9_]+\.py"]
# Files the scaffold owns. They are re-copied into an existing workspace so scaffold fixes reach old projects.
SCAFFOLD_OWNED = [
    "app/__init__.py", "app/auth.py", "app/db.py", "app/main.py", "app/migrations/000_users.sql",
    "tests/conftest.py", "tests/test_scaffold_auth.py", "pytest.ini", "requirements.txt",
]


class PathNotAllowed(ValueError):
    pass


def path_allowed(path: str, patterns: list[str]) -> bool:
    return ".." not in path.split("/") and any(re.fullmatch(p, path) for p in patterns)


class Workspace:
    """One project's working copy of the scaffold."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    @classmethod
    def create(cls, base: Path, name: str, fresh: bool = True) -> "Workspace":
        root = Path(base) / name
        if fresh and root.exists():
            shutil.rmtree(root)
        if not root.exists():
            shutil.copytree(
                SCAFFOLD_DIR, root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.db")
            )
        else:
            for rel in SCAFFOLD_OWNED:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SCAFFOLD_DIR / rel, root / rel)
        return cls(root)

    def write_files(self, files: dict[str, str], patterns: list[str] | None = None) -> list[str]:
        """Write files after checking every path first. Raises PathNotAllowed, writing nothing."""
        patterns = FILE_PATTERNS if patterns is None else patterns
        bad = [p for p in files if not path_allowed(p, patterns)]
        if bad:
            raise PathNotAllowed(f"not allowed to write: {', '.join(sorted(bad))}")
        for rel, content in files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return sorted(files)

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def files_under(self, subdir: str, suffix: str) -> dict[str, str]:
        """{relative path: content} for files in a subdirectory, sorted by name."""
        base = self.root / subdir
        if not base.exists():
            return {}
        return {
            str(p.relative_to(self.root)): p.read_text()
            for p in sorted(base.glob(f"*{suffix}"))
            if p.name != "__init__.py"
        }

    def generated_files(self) -> dict[str, str]:
        """Everything the agents wrote: routers, migrations and the independent tests."""
        out: dict[str, str] = {}
        for subdir, pattern in (("app/routers", "*.py"), ("app/migrations", "*.sql"), ("tests", "test_req_*.py")):
            for rel, content in self.files_under(subdir, pattern[1:]).items():
                if rel.endswith("000_users.sql") or (subdir == "tests" and not Path(rel).name.startswith("test_req_")):
                    continue
                out[rel] = content
        return out

    def snapshot(self, paths: list[str]) -> dict[str, str | None]:
        return {p: (self.read(p) if self.exists(p) else None) for p in paths}

    def restore(self, snap: dict[str, str | None]) -> None:
        for rel, content in snap.items():
            target = self.root / rel
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(content)

    def smoke_check(self, timeout_s: int = 60) -> tuple[bool, str]:
        """Start the app against a temp database (runs every migration) and build its OpenAPI schema.

        Catches broken imports, bad SQL and invalid response models before any test runs.
        """
        code = (
            "import os, tempfile\n"
            "os.environ['TESSERA_DB'] = os.path.join(tempfile.mkdtemp(), 'smoke.db')\n"
            "from fastapi.testclient import TestClient\n"
            "from app.main import create_app\n"
            "with TestClient(create_app()) as c:\n"
            "    r = c.get('/openapi.json')\n"
            "    assert r.status_code == 200, r.text\n"
        )
        try:
            p = subprocess.run(
                [sys.executable, "-c", code], cwd=self.root, capture_output=True, text=True,
                timeout=timeout_s, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return False, f"the app did not start within {timeout_s}s"
        if p.returncode == 0:
            return True, ""
        return False, "\n".join(p.stderr.strip().splitlines()[-12:])


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    tests: dict[str, str] = field(default_factory=dict)  # "tests/test_x.py::test_y" -> passed|failed|error|skipped
    details: dict[str, str] = field(default_factory=dict)  # same keys -> short failure message

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Runner(Protocol):
    def run_pytest(self, workdir: Path, timeout_s: int = 90) -> RunResult: ...


def parse_junit(xml_text: str) -> dict[str, str]:
    """Map each test (file::name) to its outcome from a pytest junit xml report."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for case in root.iter("testcase"):
        module = (case.get("classname") or "").replace(".", "/")
        key = f"{module}.py::{case.get('name')}" if module else str(case.get("name"))
        if case.find("failure") is not None:
            out[key] = "failed"
        elif case.find("error") is not None:
            out[key] = "error"
        elif case.find("skipped") is not None:
            out[key] = "skipped"
        else:
            out[key] = "passed"
    return out


def parse_junit_details(xml_text: str, limit: int = 600) -> dict[str, str]:
    """Map each failed or errored test to the end of its failure message."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for case in root.iter("testcase"):
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is None:
            continue
        module = (case.get("classname") or "").replace(".", "/")
        key = f"{module}.py::{case.get('name')}" if module else str(case.get("name"))
        text = (bad.text or bad.get("message") or "").strip()
        out[key] = text[-limit:]
    return out


class LocalRunner:
    """Dev-only. Do NOT point this at untrusted generated code in the hosted demo."""

    def run_pytest(self, workdir: Path, timeout_s: int = 90) -> RunResult:
        workdir = Path(workdir).resolve()
        report = workdir / ".report.xml"
        report.unlink(missing_ok=True)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
                 f"--junitxml={report}"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=workdir,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(124, e.stdout or "", f"timeout after {timeout_s}s")
        xml = report.read_text() if report.exists() else ""
        return RunResult(p.returncode, p.stdout, p.stderr, parse_junit(xml), parse_junit_details(xml))


# TODO(day 3-4): ContreeRunner using `contree-sdk` (docs: sandboxes/start/python-quickstart).
# Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID. Pattern from the docs:
#   client = Contree(); base = await client.images.use("python:3.12", strict=True)
#   result = await base.run(shell="pytest -q", disposable=True)  # exit_code / stdout / stderr
# Prepare an image with the scaffold + deps once (disposable=False), then branch per attempt.