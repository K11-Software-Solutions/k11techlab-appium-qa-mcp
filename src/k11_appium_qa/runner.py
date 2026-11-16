"""Run the project's pytest + Appium suite and return structured, per-test results.

Tests run with the project's own Python (no shell), with a small plugin on PYTHONPATH that
records the locator each failing test was looking for and saves the screen it failed on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .locators import STRATEGIES, Loc, render

PLUGIN_DIR = Path(__file__).parent / "_pytest_plugin"
SCREENS_DIR = ".qa-mcp/appium-screens"


def project_python(root: Path) -> str:
    """QA_MCP_PYTHON, else the project's .venv/venv, else this interpreter."""
    if os.environ.get("QA_MCP_PYTHON"):
        return os.environ["QA_MCP_PYTHON"]
    for venv in (".venv", "venv", "env"):
        for rel in ("Scripts/python.exe", "bin/python"):
            p = Path(root) / venv / rel
            if p.is_file():
                return str(p)
    return sys.executable


def run_tests(root: Path, targets: list[str] | None = None, keyword: str | None = None, timeout_s: int = 1800,
              extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    screens = root / SCREENS_DIR
    with tempfile.TemporaryDirectory(prefix="k11appium-") as tmp:
        results_file = Path(tmp) / "results.json"
        env = {**os.environ, **(extra_env or {})}
        env["K11_QA_RESULTS"] = str(results_file)
        env["K11_QA_SCREENS"] = str(screens)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PLUGIN_DIR), env.get("PYTHONPATH")]))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        args = [project_python(root), "-m", "pytest", "-p", "k11_appium_pytest_plugin", "-q", "--tb=short",
                "-p", "no:cacheprovider", "-o", "console_output_style=classic"]
        if keyword:
            args += ["-k", keyword]
        args += targets or []
        try:
            proc = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"error": f"Tests timed out after {timeout_s}s", "tests": []}
        except FileNotFoundError as e:
            return {"error": f"Could not start Python: {e}", "tests": []}
        if not results_file.is_file():
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
            return {"error": "pytest did not produce results. Is pytest installed in the project's environment?",
                    "exitCode": proc.returncode, "output": "\n".join(tail), "tests": []}
        data = json.loads(results_file.read_text("utf8"))

    tests = []
    for t in data["tests"]:
        item: dict[str, Any] = {"test": t["nodeid"], "name": t["name"],
                                "file": os.path.relpath(t["file"], root).replace("\\", "/"), "filePath": t["file"],
                                "line": t.get("line"), "status": t["outcome"], "duration": t.get("duration")}
        for k in ("error", "errorType", "errorLine", "locatorLine", "platform"):
            if t.get(k) is not None:
                item[k] = t[k]
        if t.get("screen"):
            item["screen"] = os.path.relpath(t["screen"], root).replace("\\", "/")
        fl = t.get("failingLocator")
        if fl and fl.get("by") in STRATEGIES and fl.get("value"):
            item["failingLocator"] = render(Loc(fl["by"], fl["value"]))
        elif fl:
            item["failingLocator"] = f'({fl.get("by")!r}, {fl.get("value")!r})'
        tests.append(item)
    counts = {s: sum(1 for t in tests if t["status"] == s) for s in ("passed", "failed", "error", "skipped")}
    return {**counts, "total": len(tests), "exitCode": data.get("exitstatus"), "tests": tests}
