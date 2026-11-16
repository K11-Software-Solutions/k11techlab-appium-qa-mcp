"""pytest plugin loaded by k11 Appium QA when it runs your tests (``-p k11_appium_pytest_plugin``).

For each failing test it records the last Appium locator that could not be found, the line in
your code that asked for it, the platform, and the screen the app was showing (its page source,
saved once when the test fails). Results are written as JSON to K11_QA_RESULTS and screens to
K11_QA_SCREENS. It changes nothing about how tests run. Self-contained on purpose: it is put on
PYTHONPATH for the project's own interpreter.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

_results: list[dict] = []
_current: dict = {"file": None, "failed": None, "driver": None}


def _record_failure(driver, by, value):
    line = None
    f = sys._getframe(2)
    test_file = _current.get("file")
    while f is not None:
        fn = os.path.normcase(os.path.abspath(f.f_code.co_filename))
        if test_file and fn == test_file:
            line = f.f_lineno
            break
        f = f.f_back
    _current["failed"] = {"by": by, "value": value, "line": line}
    _current["driver"] = driver


def _install() -> None:
    try:
        from selenium.common.exceptions import NoSuchElementException
        from selenium.webdriver.remote.webdriver import WebDriver
        from selenium.webdriver.remote.webelement import WebElement
    except ImportError:
        return
    if getattr(WebDriver.find_element, "_k11_appium", False):
        return

    def wrap(orig, is_driver):
        def find_element(self, by="id", value=None):
            try:
                el = orig(self, by, value)
            except NoSuchElementException:
                _record_failure(self if is_driver else self.parent, by, value)
                raise
            failed = _current.get("failed")
            if failed and failed["by"] == by and failed["value"] == value:
                _current["failed"] = None  # found after waiting: not the cause
            return el

        find_element._k11_appium = True
        return find_element

    WebDriver.find_element = wrap(WebDriver.find_element, True)
    WebElement.find_element = wrap(WebElement.find_element, False)


_install()


def _save_screen(nodeid: str) -> tuple[str | None, str | None]:
    driver = _current.get("driver")
    out_dir = os.environ.get("K11_QA_SCREENS")
    if driver is None or not out_dir:
        return None, None
    try:
        source = driver.page_source
    except Exception:  # noqa: BLE001 - session may be gone
        return None, None
    platform = None
    try:
        caps = getattr(driver, "capabilities", {}) or {}
        platform = str(caps.get("platformName") or caps.get("appium:platformName") or "").lower() or None
    except Exception:  # noqa: BLE001
        pass
    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)[-150:] + ".xml"
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf8") as fh:
        fh.write(source)
    return path, platform


def pytest_runtest_setup(item):
    _current["file"] = os.path.normcase(os.path.abspath(str(item.fspath)))
    _current["failed"] = None
    _current["driver"] = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.failed and rep.when == "setup") or (rep.skipped and rep.when == "setup"):
        entry = {
            "nodeid": item.nodeid,
            "name": item.name,
            "file": os.path.abspath(str(item.fspath)),
            "line": (item.location[1] or 0) + 1,
            "outcome": rep.outcome if rep.when == "call" else ("error" if rep.failed else "skipped"),
            "duration": round(rep.duration, 3),
        }
        if rep.failed and call.excinfo is not None:
            exc = call.excinfo.value
            msg = (getattr(exc, "msg", None) or str(exc) or "").strip()
            entry["error"] = f"{call.excinfo.typename}: {msg.splitlines()[0] if msg else ''}".strip().rstrip(":")
            entry["errorType"] = call.excinfo.typename
            for e in reversed(call.excinfo.traceback):
                if os.path.normcase(os.path.abspath(str(e.path))) == _current["file"]:
                    entry["errorLine"] = e.lineno + 1
                    break
            failed = _current.get("failed")
            if failed and call.excinfo.typename in ("NoSuchElementException", "TimeoutException"):
                entry["failingLocator"] = {"by": failed["by"], "value": failed["value"]}
                if failed.get("line"):
                    entry["locatorLine"] = failed["line"]
                screen, platform = _save_screen(item.nodeid)
                if screen:
                    entry["screen"] = screen
                if platform:
                    entry["platform"] = platform
        _results[:] = [r for r in _results if r["nodeid"] != item.nodeid] + [entry]


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("K11_QA_RESULTS")
    if path:
        with open(path, "w", encoding="utf8") as fh:
            json.dump({"tests": _results, "exitstatus": int(exitstatus)}, fh, indent=2)
