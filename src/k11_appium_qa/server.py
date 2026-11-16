"""MCP server: self-healing Appium locators for native and hybrid mobile apps."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import __version__, live
from .heal import fingerprint_of, heal
from .locators import CannotEvaluate, find, parse, render, unique_locators
from .runner import SCREENS_DIR, run_tests as _run_tests
from .screen import Screen, accessibility_issues, load, parse as parse_xml
from .store import BaselineStore

LANGS = ("python", "java")


def project_root() -> Path:
    return Path(os.environ.get("QA_MCP_ROOT") or os.getcwd()).resolve()


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"Path must stay inside the project: {rel}")
    return p


def _screen(screen: Optional[str]) -> Screen:
    root = project_root()
    if screen:
        p = _inside(root, screen)
        if not p.is_file():
            raise ValueError(f"Screen file not found: {screen} (saved screens are in {SCREENS_DIR}/)")
        return load(p)
    return live.current_screen(root)


def _lang(lang: Optional[str]) -> str:
    if lang and lang not in LANGS:
        raise ValueError("lang must be python or java")
    if lang:
        return lang
    root = project_root()
    return "java" if (root / "pom.xml").is_file() or (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file() else "python"


def _parse(locator: str):
    loc = parse(locator)
    if loc is None:
        raise ValueError('Unsupported locator. Use one Appium locator, e.g. (AppiumBy.ACCESSIBILITY_ID, "Login"), '
                         'AppiumBy.id("login"), @AndroidFindBy(accessibility = "login") or @iOSXCUITFindBy(id = "login").')
    return loc


def create_server() -> FastMCP:
    mcp = FastMCP(
        "k11-appium-qa",
        instructions=(
            "Self-healing locators for Appium tests of native and hybrid Android and iOS apps. Every tool works on a "
            "saved screen (Appium page source XML, e.g. the one saved when a test failed, in .qa-mcp/appium-screens/) "
            "or, when .qa-mcp/appium.yaml configures a server, on the live device. Use inspect_screen for verified "
            "locators (accessibility ids first), check_locator before using one, capture_baseline while tests pass, "
            "heal_locator when a locator breaks, run_tests to run the pytest suite. Suggestions are never applied "
            "silently: review, edit, re-run. Never weaken a test to make it pass."
        ),
    )
    mcp._mcp_server.version = __version__

    @mcp.tool(title="Inspect screen")
    def inspect_screen(screen: Optional[str] = None, lang: Optional[str] = None, interactive_only: bool = True,
                       max_elements: int = 60) -> dict:
        """List a screen's elements with locators verified to match exactly one element, most stable first
        (accessibility id, resource id, text, XPath). screen: a saved page source XML in the project; omit to read
        the live device. lang: python (default) or java, for how locators are written."""
        s = _screen(screen)
        lg = _lang(lang)
        out = []
        for e in s.elements:
            if not e.visible or (interactive_only and not e.clickable and not e.fp.get("acc")):
                continue
            uniq = unique_locators(s, e)
            out.append({**e.fp, "clickable": e.clickable, "best": render(uniq[0], lg) if uniq else None,
                        "alternatives": [render(u, lg) for u in uniq[1:4]]})
            if len(out) >= max_elements:
                break
        issues = accessibility_issues(s)
        return {"platform": s.platform, "package": s.package or None, "screen": screen or "live", "count": len(out),
                "elements": out, "accessibility_issues": len(issues),
                "note": "Elements without 'best' have no unique locator: ask for an accessibility id (contentDescription / "
                        "accessibilityIdentifier)." if any(not x["best"] for x in out) else None}

    @mcp.tool(title="Check locator")
    def check_locator(locator: str, screen: Optional[str] = None) -> dict:
        """How many elements an Appium locator matches on a screen (1 = good) and whether it's visible. Accepts Python
        (AppiumBy.X, "v"), Java AppiumBy.x("v") and @AndroidFindBy / @iOSXCUITFindBy forms."""
        loc = _parse(locator)
        s = _screen(screen)
        try:
            hits = find(s, loc)
        except CannotEvaluate as e:
            return {"locator": render(loc), "platform": s.platform, "verdict": "can't evaluate offline", "note": str(e)}
        return {"locator": render(loc), "platform": s.platform, "matches": len(hits),
                "visible": hits[0].visible if len(hits) == 1 else None,
                "verdict": "unique" if len(hits) == 1 else "not found" if not hits else "ambiguous"}

    @mcp.tool(title="Capture locator baselines")
    def capture_baseline(locators: list[str], screen: Optional[str] = None) -> dict:
        """While tests pass, record what each locator's element looks like (accessibility id, resource id, text, hint,
        class) in .qa-mcp/appium-baselines.json, per platform. heal_locator and heal-ci use these after the app
        changes. Run it on each screen your tests use (or on screens saved from passing runs)."""
        s = _screen(screen)
        saved, skipped = {}, []
        for raw in locators[:100]:
            loc = parse(raw)
            if loc is None:
                skipped.append({"locator": raw, "reason": "unsupported locator"})
                continue
            try:
                hits = find(s, loc)
            except CannotEvaluate as e:
                skipped.append({"locator": raw, "reason": str(e)})
                continue
            if len(hits) != 1:
                skipped.append({"locator": render(loc), "reason": f"matches {len(hits)} elements"})
                continue
            saved[loc] = fingerprint_of(hits[0])
        BaselineStore(project_root()).put(s.platform, saved, screen or "live")
        return {"platform": s.platform, "captured": [render(k) for k in saved], "skipped": skipped,
                "file": ".qa-mcp/appium-baselines.json"}

    @mcp.tool(title="Heal locator")
    def heal_locator(locator: str, screen: Optional[str] = None, lang: Optional[str] = None) -> dict:
        """For a locator that no longer matches (or matches several elements), find the intended element on the screen
        and suggest a verified replacement with a confidence score, reasons, the runner-up and alternatives (accessibility
        ids preferred). screen: the page source saved when the test failed (see run_tests), or omit for the live device.
        Never edits files."""
        loc = _parse(locator)
        s = _screen(screen)
        baseline = BaselineStore(project_root()).get(s.platform, loc)
        return {"screen": screen or "live", **heal(s, loc, locator, baseline, _lang(lang))}

    @mcp.tool(title="Accessibility check")
    def accessibility_check(screen: Optional[str] = None) -> dict:
        """Tappable elements a screen reader can't name, and duplicate accessibility ids. These hurt users AND make tests
        fragile (no stable accessibility id to target)."""
        s = _screen(screen)
        issues = accessibility_issues(s)
        return {"platform": s.platform, "issues": issues, "count": len(issues)}

    @mcp.tool(title="Save screen")
    def save_screen(name: str) -> dict:
        """Save the live device's current screen (page source) to .qa-mcp/appium-screens/<name>.xml, to inspect, heal or
        capture baselines later without a device."""
        import re

        if not re.fullmatch(r"[\w.-]{1,60}", name):
            raise ValueError("name: letters, digits, dot, dash and underscore only")
        root = project_root()
        d = live.driver(root)
        src = d.page_source
        parse_xml(src)  # validate
        p = root / SCREENS_DIR / f"{name}.xml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, "utf8")
        return {"saved": p.relative_to(root).as_posix()}

    @mcp.tool(title="Run tests")
    def run_tests(targets: Optional[list[str]] = None, keyword: Optional[str] = None) -> dict:
        """Run the project's pytest + Appium suite. Failures caused by a missing element include the locator, the line,
        the platform and the screen saved at that moment (pass it to heal_locator)."""
        root = project_root()
        safe = [str(_inside(root, t.split("::")[0]).relative_to(root)) + (("::" + t.split("::", 1)[1]) if "::" in t else "")
                for t in (targets or [])]
        return _run_tests(root, safe or None, keyword)

    return mcp


def serve() -> None:
    try:
        create_server().run()
    finally:
        live.close()
