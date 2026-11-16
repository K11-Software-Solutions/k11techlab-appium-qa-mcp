"""Live end to end on a real Android emulator through Appium (CI: .github/workflows/e2e-android.yml).

Skipped unless APPIUM_E2E=1. Uses the emulator's built-in Settings app, so no APK is needed:
reads the live screen, captures a baseline, then heals a locator that no longer exists.
"""

import json
import os

import anyio
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("APPIUM_E2E") != "1", reason="needs an emulator and Appium (APPIUM_E2E=1)")

CONFIG = """server: ${ENV:APPIUM_URL}
capabilities:
  platformName: Android
  appium:automationName: UiAutomator2
  appium:appPackage: com.android.settings
  appium:appActivity: .Settings
  appium:noReset: true
  appium:newCommandTimeout: 300
"""


def test_live_settings_app(tmp_path, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session

    from k11_appium_qa import live
    from k11_appium_qa.server import create_server

    (tmp_path / ".qa-mcp").mkdir()
    (tmp_path / ".qa-mcp" / "appium.yaml").write_text(CONFIG, "utf8")
    monkeypatch.setenv("QA_MCP_ROOT", str(tmp_path))
    monkeypatch.setenv("APPIUM_URL", os.environ.get("APPIUM_URL", "http://127.0.0.1:4723"))

    async def go():
        async with create_connected_server_and_client_session(create_server()._mcp_server) as c:
            async def call(name, args):
                r = await c.call_tool(name, args)
                assert not r.isError, r.content[0].text
                return json.loads(r.content[0].text)

            screen = await call("inspect_screen", {"interactive_only": False, "max_elements": 200})
            assert screen["platform"] == "android" and screen["count"] > 3
            saved = await call("save_screen", {"name": "settings"})
            assert saved["saved"] == ".qa-mcp/appium-screens/settings.xml"
            # pick a real element with a unique, stable locator on the live screen
            target = next(e for e in screen["elements"] if e.get("best") and (e.get("rid") or e.get("acc")))
            loc = target["best"]
            assert (await call("check_locator", {"locator": loc}))["verdict"] == "unique"
            cap = await call("capture_baseline", {"locators": [loc]})
            assert cap["captured"] == [loc]
            # the same element under an old name: heal it back from the baseline, live and from the saved screen
            from k11_appium_qa.locators import Loc, parse, render

            real = parse(loc)
            broken = render(Loc(real.strategy, real.value + "_old"))
            from k11_appium_qa.store import BaselineStore

            store = BaselineStore(tmp_path)
            data = store.load()
            data["android"][f"{real.strategy}={real.value}_old"] = data["android"][f"{real.strategy}={real.value}"]
            store.path.write_text(json.dumps(data), "utf8")
            for args in ({"locator": broken}, {"locator": broken, "screen": ".qa-mcp/appium-screens/settings.xml"}):
                healed = await call("heal_locator", args)
                assert healed["status"] == "healed" and healed["confidence"] >= 0.9, healed
                assert parse(healed["suggestion"]) is not None

    try:
        anyio.run(go)
    finally:
        live.close()
