"""A mobile screen: Appium's page source (Android UiAutomator2 or iOS XCUITest XML).

Every element becomes a fingerprint with the same keys on both platforms, so healing
compares like with like:

    cls     Android class (android.widget.Button) / iOS type (XCUIElementTypeButton)
    acc     accessibility id: Android content-desc / iOS name
    rid     Android resource-id (com.shop:id/login); iOS has none
    text    Android text / iOS label (or value)
    hint    Android hint / iOS placeholderValue
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

ANDROID, IOS = "android", "ios"
_ROOTS = {"hierarchy", "AppiumAUT"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=False)


@dataclass
class Element:
    node: etree._Element
    fp: dict[str, str]
    visible: bool
    clickable: bool
    bounds: tuple[int, int, int, int] | None = None

    @property
    def short_rid(self) -> str:
        rid = self.fp.get("rid", "")
        return rid.split(":id/", 1)[1] if ":id/" in rid else rid


@dataclass
class Screen:
    platform: str
    tree: etree._Element
    elements: list[Element] = field(default_factory=list)
    package: str = ""
    source: str = ""

    def element_for(self, node) -> Element | None:
        for e in self.elements:
            if e.node is node:
                return e
        return None


def _bounds_android(b: str) -> tuple[int, int, int, int] | None:
    import re

    m = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def parse(xml: str | bytes, source: str = "") -> Screen:
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        root = etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"Not a valid Appium page source: {e}") from None
    first = next((c for c in root.iter() if c.tag not in _ROOTS), None) if root.tag in _ROOTS else root
    platform = IOS if (str(first.tag).startswith("XCUIElementType") if first is not None else False) else ANDROID
    screen = Screen(platform, root, source=source)
    for node in root.iter():
        if not isinstance(node.tag, str) or node.tag in _ROOTS:
            continue
        a = node.attrib
        if platform == ANDROID:
            fp = {"cls": a.get("class") or node.tag, "acc": a.get("content-desc", ""), "rid": a.get("resource-id", ""),
                  "text": a.get("text", ""), "hint": a.get("hint", "")}
            screen.package = screen.package or a.get("package", "")
            bounds = _bounds_android(a.get("bounds", ""))
            visible = a.get("displayed", "true") != "false" and (bounds is None or (bounds[2] > bounds[0] and bounds[3] > bounds[1]))
            clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        else:
            fp = {"cls": a.get("type") or node.tag, "acc": a.get("name", ""), "rid": "",
                  "text": a.get("label", "") or a.get("value", ""), "hint": a.get("placeholderValue", "")}
            try:
                x, y, w, h = (int(float(a.get(k, "0"))) for k in ("x", "y", "width", "height"))
                bounds = (x, y, x + w, y + h)
            except ValueError:
                bounds = None
            visible = a.get("visible", "true") == "true"
            clickable = fp["cls"] in {"XCUIElementTypeButton", "XCUIElementTypeLink", "XCUIElementTypeCell",
                                      "XCUIElementTypeTextField", "XCUIElementTypeSecureTextField", "XCUIElementTypeSwitch",
                                      "XCUIElementTypeTab", "XCUIElementTypeSegmentedControl"}
        screen.elements.append(Element(node, {k: v for k, v in fp.items() if v}, visible, clickable, bounds))
    if not screen.elements:
        raise ValueError("The page source has no elements")
    return screen


def load(path: Path) -> Screen:
    return parse(Path(path).read_bytes(), str(path))


def accessibility_issues(screen: Screen) -> list[dict]:
    """Tappable elements a screen reader can't name, and duplicate accessibility ids."""
    issues: list[dict] = []
    seen: dict[str, int] = {}
    for e in screen.elements:
        if not e.visible:
            continue
        acc = e.fp.get("acc")
        if acc:
            seen[acc] = seen.get(acc, 0) + 1
        if e.clickable and not acc and not e.fp.get("text"):
            issues.append({"issue": "no accessible name", "element": e.fp.get("cls"), "rid": e.fp.get("rid"),
                           "bounds": e.bounds, "fix": "add contentDescription (Android) / accessibilityLabel (iOS)"})
    for acc, n in seen.items():
        if n > 1:
            issues.append({"issue": "duplicate accessibility id", "acc": acc, "count": n,
                           "fix": "accessibility ids should be unique on a screen, or tests can't target them"})
    return issues
