"""Baseline fingerprints, kept in the project at .qa-mcp/appium-baselines.json, per platform."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .locators import Loc, key

FILE = ".qa-mcp/appium-baselines.json"


class BaselineStore:
    def __init__(self, root: Path):
        self.path = Path(root) / FILE

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf8"))
        except (OSError, ValueError):
            return {}

    def get(self, platform: str, loc: Loc) -> dict[str, str] | None:
        entry = self.load().get(platform, {}).get(key(loc))
        return entry["fingerprint"] if entry else None

    def put(self, platform: str, entries: dict[Loc, dict[str, str]], screen: str = "") -> None:
        data = self.load()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        bucket = data.setdefault(platform, {})
        for loc, fp in entries.items():
            bucket[key(loc)] = {"fingerprint": fp, "capturedAt": now, **({"screen": screen} if screen else {})}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf8")
