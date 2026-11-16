"""Command line: start the MCP server (default) or heal broken locators in CI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__

USAGE = f"""k11-appium-qa-mcp {__version__}

Usage:
  k11-appium-qa-mcp                    start the MCP server on stdio
  k11-appium-qa-mcp heal-ci [opts]     run the pytest + Appium suite, heal broken locators, report
  k11-appium-qa-mcp inspect FILE.xml   list a saved screen's elements with verified locators
  k11-appium-qa-mcp --version

heal-ci options:
  --apply                 write confident changes (default: dry run, suggestions only)
  --min-confidence 0.7    threshold for automatic changes
  --no-verify             don't re-run healed tests (not recommended)
  --report-dir PATH       default .qa-mcp/heal-report
  --fail-on LEVEL         unresolved (default: exit 1 if anything needs a person) | never

The project is QA_MCP_ROOT or the current directory.
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if not argv:
        from .server import serve

        serve()
        return 0
    if argv[0] in ("--version", "-v"):
        print(__version__)
        return 0
    if argv[0] in ("--help", "-h", "help"):
        print(USAGE)
        return 0
    root = Path(os.environ.get("QA_MCP_ROOT") or os.getcwd()).resolve()
    if argv[0] == "inspect" and len(argv) == 2:
        from .locators import render, unique_locators
        from .screen import accessibility_issues, load

        s = load(Path(argv[1]))
        print(f"{s.platform} screen, {len(s.elements)} elements")
        for e in s.elements:
            if e.visible and (e.clickable or e.fp.get("acc")):
                u = unique_locators(s, e)
                label = e.fp.get("acc") or e.fp.get("text") or e.fp.get("rid") or e.fp.get("cls")
                print(f"  {label[:40]:40} {render(u[0]) if u else '(no unique locator)'}")
        for i in accessibility_issues(s):
            print(f"  ! {i['issue']}: {i.get('rid') or i.get('acc') or i.get('element')}")
        return 0
    if argv[0] != "heal-ci":
        print(f"Unknown command: {argv[0]}\n\n{USAGE}", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(prog="k11-appium-qa-mcp heal-ci")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--min-confidence", type=float)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--report-dir")
    p.add_argument("--fail-on", default="unresolved", choices=["unresolved", "never"])
    a = p.parse_args(argv[1:])
    from .healci import heal_ci

    try:
        res = heal_ci(root, a.apply, a.min_confidence, a.report_dir, not a.no_verify, log=lambda m: print(m, file=sys.stderr))
    except RuntimeError as e:
        print(f"heal-ci failed: {e}", file=sys.stderr)
        return 1
    c = res["report"]["counts"]
    print(f"{c['applied']} healed and verified, {c['suggested']} suggested, {c['reverted']} reverted, "
          f"{c['needs_review']} need review, {c['not_found']} not found. Report: {res['markdownPath']}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf8") as fh:
            fh.write(Path(res["markdownPath"]).read_text("utf8") + "\n")
    unresolved = c["reverted"] + c["needs_review"] + c["not_found"] + len(res["report"]["otherFailures"])
    return 1 if a.fail_on == "unresolved" and unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
