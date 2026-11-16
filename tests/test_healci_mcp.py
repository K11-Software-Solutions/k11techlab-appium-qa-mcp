"""heal-ci end to end on a pytest + Appium project (a fake driver serves saved screens), the CLI and MCP tools."""

import json
import shutil
from pathlib import Path

import anyio
import pytest

from k11_appium_qa.cli import main
from k11_appium_qa.healci import heal_ci

HERE = Path(__file__).parent
SCREENS = HERE / "fixtures" / "screens"


@pytest.fixture
def project(tmp_path, monkeypatch):
    dest = tmp_path / "shop-app"
    shutil.copytree(HERE / "fixtures" / "shop-app", dest, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SCREENS, dest / "screens")
    monkeypatch.setenv("QA_MCP_ROOT", str(dest))
    return dest


def screen_env(project: Path, name: str) -> dict:
    return {"K11_FAKE_SCREEN": str(project / "screens" / name)}


async def _tools(calls):
    from mcp.shared.memory import create_connected_server_and_client_session

    from k11_appium_qa.server import create_server

    async with create_connected_server_and_client_session(create_server()._mcp_server) as c:
        out = [{t.name for t in (await c.list_tools()).tools}]
        for name, args in calls:
            r = await c.call_tool(name, args)
            out.append({"error": r.content[0].text} if r.isError else json.loads(r.content[0].text))
        return out


def capture_v1_baselines(project: Path):
    locs = ['(AppiumBy.ID, "et_email")', '(AppiumBy.ACCESSIBILITY_ID, "password-input")',
            '(AppiumBy.ACCESSIBILITY_ID, "login-button")', '(AppiumBy.ACCESSIBILITY_ID, "signup-button")', '(AppiumBy.ID, "title")']
    tools, res = anyio.run(_tools, [("capture_baseline", {"locators": locs, "screen": "screens/android_login_v1.xml"})])
    assert len(res["captured"]) == 5 and res["platform"] == "android"


def test_mcp_tools_on_saved_screens(project):
    tools, insp, chk, heal, a11y, outside, live = anyio.run(_tools, [
        ("inspect_screen", {"screen": "screens/android_login_v2.xml"}),
        ("check_locator", {"locator": 'AppiumBy.accessibilityId("login-button")', "screen": "screens/android_login_v2.xml"}),
        ("heal_locator", {"locator": '@AndroidFindBy(accessibility = "login-button")', "screen": "screens/android_login_v2.xml", "lang": "java"}),
        ("accessibility_check", {"screen": "screens/android_login_v2.xml"}),
        ("inspect_screen", {"screen": "../outside.xml"}),
        ("inspect_screen", {}),
    ])
    assert tools == {"inspect_screen", "check_locator", "capture_baseline", "heal_locator", "accessibility_check",
                     "save_screen", "run_tests"}
    assert insp["platform"] == "android" and insp["accessibility_issues"] == 1
    assert '(AppiumBy.ACCESSIBILITY_ID, "sign-in-button")' in [e["best"] for e in insp["elements"]]
    assert chk["verdict"] == "not found"
    assert heal["suggestion"] == 'AppiumBy.accessibilityId("sign-in-button")'
    assert a11y["count"] == 1
    assert "inside the project" in outside["error"]
    assert "No live device configured" in live["error"]


def test_heal_ci_heals_a_redesign_across_rounds(project):
    capture_v1_baselines(project)
    res = heal_ci(project, apply=True, extra_env=screen_env(project, "android_login_v2.xml"))
    rep = res["report"]
    by = {(i["test"].split("::")[-1], i["locator"]): i for i in rep["items"]}
    # test_log_in broke on three locators; each was healed in turn and the test now passes
    for loc, new in [('(AppiumBy.ID, "et_email")', '(AppiumBy.ACCESSIBILITY_ID, "email-field")'),
                     ('(AppiumBy.ACCESSIBILITY_ID, "password-input")', '(AppiumBy.ACCESSIBILITY_ID, "password-field")'),
                     ('(AppiumBy.ACCESSIBILITY_ID, "login-button")', '(AppiumBy.ACCESSIBILITY_ID, "sign-in-button")')]:
        it = by[("test_log_in", loc)]
        assert (it["outcome"], it["suggestion"], it["verified"]) == ("applied", new, True), json.dumps(
            [{k: i.get(k) for k in ("test", "locator", "outcome", "suggestion", "verified", "note")} for i in rep["items"]], indent=1)
    page = (project / "tests/pages/login_page.py").read_text("utf8")
    assert 'EMAIL = (AppiumBy.ACCESSIBILITY_ID, "email-field")' in page and '"sign-in-button"' in page
    # the sign-up button lost its accessibility id: not a confident change, left for a person
    signup = by[("test_open_sign_up", '(AppiumBy.ACCESSIBILITY_ID, "signup-button")')]
    assert signup["outcome"] in ("needs_review", "reverted", "applied")
    if signup["outcome"] != "applied":
        assert 'SIGN_UP = (AppiumBy.ACCESSIBILITY_ID, "signup-button")' in page
    # the changed title is a real failure, not a locator problem: untouched
    assert [f["test"].split("::")[-1] for f in rep["otherFailures"]] == ["test_title"]
    assert rep["tool"].startswith("k11-appium-qa-mcp heal-ci")       # Release Gate reads it as ui_heal evidence
    md = Path(res["markdownPath"]).read_text("utf8")
    assert "Mobile locator healing report" in md and "android" in md


def test_heal_ci_dry_run_changes_nothing_and_cli(project, capsys, monkeypatch):
    capture_v1_baselines(project)
    before = (project / "tests/pages/login_page.py").read_text("utf8")
    res = heal_ci(project, apply=False, extra_env=screen_env(project, "android_login_v2.xml"))
    assert res["report"]["mode"] == "dry-run" and res["report"]["counts"]["suggested"] >= 1
    assert (project / "tests/pages/login_page.py").read_text("utf8") == before
    monkeypatch.setenv("K11_FAKE_SCREEN", str(project / "screens" / "android_login_v2.xml"))
    assert main(["heal-ci", "--fail-on", "never"]) == 0
    assert main(["heal-ci"]) == 1          # something needs a person (the title failure)
    assert "suggested" in capsys.readouterr().out
    assert main(["inspect", str(project / "screens" / "ios_login_v2.xml")]) == 0
    assert "ios screen" in capsys.readouterr().out


def test_same_length_edit_in_the_same_second_is_not_hidden_by_bytecode(project):
    """Regression: a same-size rewrite within one second used to re-run stale bytecode and revert a good fix."""
    import os

    from k11_appium_qa.healci import _write

    page = project / "tests/pages/login_page.py"
    cache = page.parent / "__pycache__"
    cache.mkdir(exist_ok=True)
    stale = cache / "login_page.cpython-311.pyc"
    stale.write_bytes(b"stale")
    st = page.stat()
    _write(page, page.read_text("utf8").replace("password-input", "password-field"))
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns))     # same size, same mtime: worst case
    assert page.stat().st_size == st.st_size and not stale.exists()
