"""heal-ci: heal broken Appium locators in CI, with an audit trail.

  run the suite (the plugin saves each failing test's screen) → for each broken locator:
  heal against the screen the test failed on and the committed baseline → apply only
  confident changes (test files and Page Objects) → re-run the affected tests on the
  device → revert any change whose tests still fail → write heal-report.json/.md.

Healing needs no device: the screen is the page source saved at the moment of failure.
Only the verification re-run needs the device or emulator the tests already use.
Tests that share a broken locator are healed together: one change, kept only if all of
them pass. The report format matches k11 Playwright QA and Selenium QA, so k11 Release
Gate reads it as UI evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .heal import heal
from .locators import Loc, parse, render
from .rewrite import CannotRewrite, Replacement, locate_in_project, replace_locator
from .runner import run_tests
from .screen import load
from .store import BaselineStore

DEFAULT_MIN_CONFIDENCE = 0.7
OUTCOMES = ("applied", "suggested", "reverted", "needs_review", "not_found", "skipped")


@dataclass
class HealItem:
    test: str
    file: str
    locator: str
    line: int | None = None
    platform: str | None = None
    screen: str | None = None
    outcome: str = "skipped"
    confidence: float | None = None
    suggestion: str | None = None
    reasons: list[str] = field(default_factory=list)
    runnerUp: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    replacements: list[dict] = field(default_factory=list)
    verified: bool | None = None
    note: str | None = None


def _read(p: Path) -> str:
    with open(p, encoding="utf8", newline="") as fh:
        return fh.read()


def _write(p: Path, s: str) -> None:
    with open(p, "w", encoding="utf8", newline="") as fh:
        fh.write(s)


def load_config(root: Path) -> dict[str, Any]:
    f = Path(root) / ".qa-mcp/heal.config.json"
    return json.loads(f.read_text("utf8")) if f.is_file() else {}


def heal_ci(root: Path, apply: bool, min_confidence: float | None = None, report_dir: str | None = None,
            verify: bool = True, log: Callable[[str], None] = lambda m: None,
            extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    cfg = load_config(root)
    min_conf = min_confidence if min_confidence is not None else cfg.get("minConfidence", DEFAULT_MIN_CONFIDENCE)
    store = BaselineStore(root)

    log("Running tests…")
    run = run_tests(root, extra_env=extra_env)
    if run.get("error"):
        raise RuntimeError(f"{run['error']}\n{run.get('output', '')}".strip())
    failing = [t for t in run["tests"] if t["status"] in ("failed", "error")]
    items: list[HealItem] = []
    other: list[dict] = []
    originals: dict[Path, str] = {}
    current: dict[Path, str] = {}
    applied: dict[Loc, tuple[Loc, list[HealItem], list[Path]]] = {}
    attempted: set[Loc] = set()

    def heal_failures(tests: list[dict], first_pass: bool) -> int:
        """Heal the broken locators these failures point at; returns how many changes were applied."""
        groups: dict[Loc, list[tuple[dict, HealItem]]] = {}
        for t in tests:
            if not t.get("failingLocator"):
                if first_pass:
                    other.append({"test": t["test"], "file": t["file"], "error": t.get("error")})
                continue
            broken = parse(t["failingLocator"])
            if broken is not None and broken in attempted:
                continue
            item = HealItem(test=t["test"], file=t["file"], locator=t["failingLocator"],
                            line=t.get("locatorLine") or t.get("errorLine"), platform=t.get("platform"), screen=t.get("screen"))
            items.append(item)
            if broken is None:
                item.note = "Locator form not supported for healing"
                continue
            groups.setdefault(broken, []).append((t, item))
        n_applied = 0
        for broken, members in groups.items():
            attempted.add(broken)
            healed_items: list[HealItem] = []
            results = {}
            for t, item in members:
                if not item.screen:
                    item.note = "No screen was saved for this failure (the session may have ended before the test failed)."
                    continue
                try:
                    screen = load(root / item.screen)
                except (OSError, ValueError) as e:
                    item.note = f"Saved screen can't be read: {e}"
                    continue
                item.platform = item.platform or screen.platform
                log(f"Healing {item.locator} on {item.screen}")
                r = heal(screen, broken, item.locator, store.get(screen.platform, broken))
                results[id(item)] = r
                cands = r.get("candidates") or []
                item.confidence, item.suggestion = r.get("confidence"), r.get("suggestion")
                item.reasons, item.candidates = r.get("reasons") or [], cands
                item.runnerUp = {"locator": cands[1]["locator"], "score": cands[1]["score"]} if len(cands) > 1 else None
                status = r["status"]
                if status == "healthy":
                    item.note = "Locator matches on the saved screen; the failure has another cause (timing, a different screen)."
                    continue
                if status == "not_found":
                    item.outcome, item.note = "not_found", r.get("note")
                    continue
                if status != "healed" or not item.suggestion or (item.confidence or 0) < min_conf:
                    item.outcome = "needs_review"
                    item.note = f"Confidence below the {min_conf} threshold for automatic changes." if status == "healed" else r.get("note")
                    continue
                healed_items.append(item)
            if not healed_items:
                continue
            unresolved = [it for _, it in members if it not in healed_items and not (it.note or "").startswith("Locator matches")]
            suggestions = list(dict.fromkeys(it.suggestion for it in healed_items))
            if len(suggestions) > 1:
                options = [[it.suggestion, *results[id(it)].get("alternatives", [])] for it in healed_items]
                shared = next((o for o in options[0] if all(o in opts for opts in options)), None)
                if shared:
                    for it in healed_items:
                        it.suggestion = shared
                    suggestions = [shared]
            if len(suggestions) > 1 or unresolved:
                why = (f"Tests using this locator were healed to different elements ({'; '.join(suggestions)})."
                       if len(suggestions) > 1 else "Other tests using this locator could not be healed with confidence.")
                for it in healed_items:
                    it.outcome, it.note = "needs_review", why
                continue
            new = parse(suggestions[0])
            prefer = Path(members[0][0]["filePath"]) if members[0][0].get("filePath") else None
            reps: list[Replacement] = []
            new_code: dict[Path, str] = {}
            try:
                for f in locate_in_project(root, broken, prefer):
                    code, r = replace_locator(current.get(f) or _read(f), broken, new, f.relative_to(root).as_posix())
                    if r:
                        new_code[f], reps = code, reps + r
            except CannotRewrite as e:
                for it in healed_items:
                    it.outcome, it.note = "needs_review", str(e)
                continue
            if not reps:
                for it in healed_items:
                    it.outcome, it.note = "needs_review", "Locator text not found in the project source (built dynamically?)."
                continue
            for it in healed_items:
                it.replacements = [asdict(x) for x in reps]
            if not apply:
                for it in healed_items:
                    it.outcome = "suggested"
                continue
            for f, code in new_code.items():
                originals.setdefault(f, current.get(f) or _read(f))
                _write(f, code)
                current[f] = code
            for it in healed_items:
                it.outcome = "applied"
            applied[broken] = (new, healed_items, list(new_code))
            n_applied += 1
        return n_applied

    heal_failures(failing, first_pass=True)

    if apply and verify and applied:
        # Re-run the tests that use healed locators. A test that now gets further and fails on another
        # broken locator is healed again (several locators can break in one redesign); then every change
        # is kept only if all the tests using it pass.
        passed: set[str] = set()
        for rnd in range(1, 6):
            log(f"Re-running healed tests on the device (round {rnd})…")
            nodes = sorted({it.test for _, its, _ in applied.values() for it in its})
            rerun = run_tests(root, targets=nodes, extra_env=extra_env)
            passed = {r["test"] for r in rerun.get("tests", []) if r["status"] == "passed"}
            still = [r for r in rerun.get("tests", []) if r["status"] in ("failed", "error")]
            if not heal_failures([r for r in still if r.get("failingLocator")], first_pass=False):
                break
        dropped = []
        for broken in list(applied):
            new, its, _files = applied[broken]
            ok = all(it.test in passed for it in its)
            for it in its:
                it.verified = ok
                if not ok:
                    it.outcome = "reverted"
                    it.note = "A test using this healed locator still failed on re-run; the change was reverted."
            if not ok:
                dropped.append(broken)
                del applied[broken]
        if dropped:
            for f, orig in originals.items():
                code = orig
                for broken, (new, _its, files) in applied.items():
                    if f in files:
                        code, _ = replace_locator(code, broken, new, f.name)
                _write(f, code)
                current[f] = code

    counts = {o: sum(1 for i in items if i.outcome == o) for o in OUTCOMES}
    report = {
        "tool": f"k11-appium-qa-mcp heal-ci {__version__}",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "apply" if apply else "dry-run",
        "minConfidence": min_conf,
        "testsRun": run.get("total", 0),
        "failures": len(failing),
        "items": [asdict(i) for i in items],
        "otherFailures": other,
        "counts": counts,
    }
    out_dir = root / (report_dir or ".qa-mcp/heal-report")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "heal-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf8")
    (out_dir / "heal-report.md").write_text(to_markdown(report), "utf8")
    return {"report": report, "markdownPath": str(out_dir / "heal-report.md"), "jsonPath": str(out_dir / "heal-report.json")}


def _code(s: str | None) -> str:
    return f"`{s.replace('`', chr(39))}`" if s else "–"


def _pct(n: float | None) -> str:
    return "–" if n is None else f"{round(n * 100)}%"


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def to_markdown(r: dict[str, Any]) -> str:
    c = r["counts"]
    L = ["## Mobile locator healing report", ""]
    L.append(f"{_n(r['testsRun'], 'test', 'tests')} run, {r['failures']} failed. "
             f"**{c['applied']} healed and verified**, {c['suggested']} suggested, {c['reverted']} reverted, "
             f"{_n(c['needs_review'], 'needs', 'need')} review, {c['not_found']} not found, "
             f"{_n(len(r['otherFailures']), 'failure', 'failures')} not caused by locators.")
    L += ["", f"Mode: **{r['mode']}**. Changes are applied only when confidence is at least **{_pct(r['minConfidence'])}**, "
              "clearly ahead of the runner-up, and every test using the locator passes on re-run on the device.", ""]
    changed = [i for i in r["items"] if i["outcome"] in ("applied", "suggested")]
    if changed:
        L += ["### Changes" if r["mode"] == "apply" else "### Suggested changes", "",
              "| Test | Platform | Where | Old locator | New locator | Confidence | Runner-up | Re-run |",
              "|---|---|---|---|---|---|---|---|"]
        for i in changed:
            where = ", ".join(sorted({f"{x['file']}:{x['line']}" for x in i["replacements"]}))
            ru = f"{_code(i['runnerUp']['locator'])} ({_pct(i['runnerUp']['score'])})" if i.get("runnerUp") else "–"
            rerun = "passed" if i.get("verified") is True else "failed" if i.get("verified") is False else "not run"
            L.append(f"| {i['test']} | {i.get('platform') or '–'} | {where} | {_code(i['locator'])} | {_code(i['suggestion'])} | "
                     f"{_pct(i['confidence'])} | {ru} | {rerun} |")
        L.append("")
        for i in changed:
            L += [f"<details><summary>Why {_code(i['suggestion'])} replaces {_code(i['locator'])}</summary>", "",
                  f"Screen: `{i.get('screen') or '–'}`", ""]
            L += [f"- {reason}" for reason in i["reasons"]]
            L += ["", "</details>", ""]
    review = [i for i in r["items"] if i["outcome"] not in ("applied", "suggested")]
    if review:
        L += ["### Needs a person", "", "| Test | Locator | Outcome | Best candidate | Note |", "|---|---|---|---|---|"]
        for i in review:
            best = i["candidates"][0] if i.get("candidates") else None
            bc = f"{_code(best['locator'])} ({_pct(best['score'])})" if best else "–"
            L.append(f"| {i['test']} | {_code(i['locator'])} | {i['outcome'].replace('_', ' ')} | {bc} | {i.get('note') or ''} |")
        L.append("")
    if r["otherFailures"]:
        L += ["### Failures not caused by locators", "", "These may be real bugs and were not touched:", ""]
        L += [f"- **{f['test']}**: {f.get('error') or 'see test output'}" for f in r["otherFailures"]]
        L.append("")
    L += [f"<sub>Generated by {r['tool']} at {r['generatedAt']}. Full evidence: heal-report.json.</sub>", ""]
    return "\n".join(L)
