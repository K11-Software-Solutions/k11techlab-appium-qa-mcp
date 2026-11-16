"""Optional live session: read the current screen from a running Appium server.

Configured in .qa-mcp/appium.yaml (secrets via ${ENV:NAME}):

    server: http://127.0.0.1:4723
    capabilities:
      platformName: Android
      appium:automationName: UiAutomator2
      appium:appPackage: com.k11.shop
      appium:appActivity: .MainActivity

Needs the Appium Python client: pip install "k11-appium-qa-mcp[live]". Every tool also works
on a saved screen (page source XML), with no device at all.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .screen import Screen, parse

CONFIG = ".qa-mcp/appium.yaml"
_session: dict[str, Any] = {"driver": None, "key": None}


def _sub_env(v: Any) -> Any:
    if isinstance(v, str):
        def rep(m):
            if m.group(1) not in os.environ:
                raise ValueError(f"Environment variable {m.group(1)} is not set")
            return os.environ[m.group(1)]
        return re.sub(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}", rep, v)
    if isinstance(v, dict):
        return {k: _sub_env(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sub_env(x) for x in v]
    return v


def config(root: Path) -> dict | None:
    p = Path(root) / CONFIG
    if not p.is_file():
        return None
    data = yaml.safe_load(p.read_text("utf8")) or {}
    if "capabilities" not in data:
        raise ValueError(f"{CONFIG} needs 'capabilities'")
    return data


def driver(root: Path):
    cfg = config(root)
    if cfg is None:
        raise ValueError(f"No live device configured. Pass a saved screen (page source XML), or create {CONFIG} "
                         "with the Appium server URL and capabilities.")
    try:
        from appium import webdriver
        from appium.options.common.base import AppiumOptions
    except ImportError:
        raise ValueError('Live sessions need the Appium client: pip install "k11-appium-qa-mcp[live]"') from None
    key = repr(cfg)
    if _session["driver"] is not None and _session["key"] == key:
        try:
            _session["driver"].current_package if "android" in str(cfg["capabilities"].get("platformName", "")).lower() else None
            return _session["driver"]
        except Exception:  # noqa: BLE001 - session gone: reconnect
            close()
    opts = AppiumOptions()
    opts.load_capabilities(_sub_env(cfg["capabilities"]))
    _session["driver"] = webdriver.Remote(_sub_env(cfg.get("server", "http://127.0.0.1:4723")), options=opts)
    _session["key"] = key
    return _session["driver"]


def current_screen(root: Path) -> Screen:
    d = driver(root)
    return parse(d.page_source, "live")


def close() -> None:
    d = _session.get("driver")
    _session["driver"] = None
    if d is not None:
        try:
            d.quit()
        except Exception:  # noqa: BLE001
            pass
