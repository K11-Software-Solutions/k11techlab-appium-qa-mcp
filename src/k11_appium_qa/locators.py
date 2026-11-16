"""Appium locators: parse (Python and Java forms), evaluate against a screen offline, render.

A locator is (strategy, value) with Appium's strategy names:
    accessibility id · id · xpath · class name · name ·
    -android uiautomator · -ios predicate string · -ios class chain

Evaluation runs against the page source, so a locator can be checked and healed against
the exact screen a test failed on, with no device. UiAutomator selectors and iOS
predicates are evaluated for their common forms; anything else is reported as
"can't evaluate offline" rather than guessed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .screen import ANDROID, IOS, Element, Screen

STRATEGIES = ("accessibility id", "id", "xpath", "class name", "name", "-android uiautomator",
              "-ios predicate string", "-ios class chain")
# Python constants (AppiumBy.X and selenium By.X)
PY_CONST = {"ACCESSIBILITY_ID": "accessibility id", "ID": "id", "XPATH": "xpath", "CLASS_NAME": "class name",
            "NAME": "name", "ANDROID_UIAUTOMATOR": "-android uiautomator", "IOS_PREDICATE": "-ios predicate string",
            "IOS_CLASS_CHAIN": "-ios class chain"}
PY_CONST_REV = {v: k for k, v in PY_CONST.items()}
# Java AppiumBy / MobileBy / By factory methods
JAVA_METHOD = {"accessibilityId": "accessibility id", "AccessibilityId": "accessibility id", "id": "id",
               "xpath": "xpath", "className": "class name", "name": "name",
               "androidUIAutomator": "-android uiautomator", "AndroidUIAutomator": "-android uiautomator",
               "iOSNsPredicateString": "-ios predicate string", "iOSClassChain": "-ios class chain"}
JAVA_METHOD_REV = {"accessibility id": "accessibilityId", "id": "id", "xpath": "xpath", "class name": "className",
                   "name": "name", "-android uiautomator": "androidUIAutomator",
                   "-ios predicate string": "iOSNsPredicateString", "-ios class chain": "iOSClassChain"}
# @AndroidFindBy / @iOSXCUITFindBy keys
ANDROID_KEYS = {"accessibility": "accessibility id", "id": "id", "xpath": "xpath", "className": "class name",
                "uiAutomator": "-android uiautomator"}
IOS_KEYS = {"accessibility": "accessibility id", "id": "id", "xpath": "xpath", "className": "class name",
            "iOSNsPredicate": "-ios predicate string", "iOSClassChain": "-ios class chain"}


class CannotEvaluate(ValueError):
    pass


@dataclass(frozen=True)
class Loc:
    strategy: str
    value: str


# ---- parsing ------------------------------------------------------------------------------------

JSTR = r'"(?:[^"\\\n]|\\.)*"'
_PY = re.compile(r"""^\(?\s*(?:AppiumBy|MobileBy|By)\s*\.\s*([A-Z_]+)\s*,\s*(?:value\s*=\s*)?(.+?)\s*\)?\s*$""", re.S)
_PY_STR = re.compile(r"""^\(?\s*(['"])([-a-z ]+)\1\s*,\s*(.+?)\s*\)?\s*$""", re.S)
_JAVA = re.compile(rf"^(?:AppiumBy|MobileBy|By)\s*\.\s*(\w+)\s*\(\s*({JSTR})\s*\)\s*;?$", re.S)
_ANNO = re.compile(r"^@?(AndroidFindBy|iOSXCUITFindBy)\s*\((.*)\)\s*$", re.S)
_TOSTRING = re.compile(r"^By\.(\w+):\s?(.+)$", re.S)  # Selenium's By.toString / error text, e.g. "By.id: login"


def java_unescape(lit: str) -> str | None:
    if len(lit) < 2 or lit[0] != '"' or lit[-1] != '"':
        return None
    body, out, i = lit[1:-1], [], 0
    esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            out.append(esc.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def javaq(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def pyq(s: str) -> str:
    if '"' in s and "'" not in s:
        return "'" + s.replace("\\", "\\\\") + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_annotation_args(kind: str, args: str) -> Loc | None:
    keys = ANDROID_KEYS if kind == "AndroidFindBy" else IOS_KEYS
    pairs = dict(re.findall(rf"(\w+)\s*=\s*({JSTR})", args))
    for key, strategy in keys.items():
        if key in pairs:
            v = java_unescape(pairs[key])
            return Loc(strategy, v) if v else None
    return None


def parse(text: str) -> Loc | None:
    """One locator in any common form: (AppiumBy.ID, "x"), AppiumBy.accessibilityId("x"),
    @AndroidFindBy(id = "x"), By.id: x, or {"strategy": ..., "value": ...}-style "strategy=value"."""
    s = (text or "").strip()
    m = _PY.match(s)
    if m and m.group(1) in PY_CONST:
        try:
            v = ast.literal_eval(m.group(2))
        except (ValueError, SyntaxError):
            return None
        return Loc(PY_CONST[m.group(1)], v) if isinstance(v, str) and v else None
    m = _PY_STR.match(s)
    if m and m.group(2) in STRATEGIES:
        try:
            v = ast.literal_eval(m.group(3))
        except (ValueError, SyntaxError):
            return None
        return Loc(m.group(2), v) if isinstance(v, str) and v else None
    m = _JAVA.match(s)
    if m and m.group(1) in JAVA_METHOD:
        v = java_unescape(m.group(2))
        return Loc(JAVA_METHOD[m.group(1)], v) if v else None
    m = _ANNO.match(s)
    if m:
        return parse_annotation_args(m.group(1), m.group(2))
    m = _TOSTRING.match(s)
    if m and m.group(1) in JAVA_METHOD:
        return Loc(JAVA_METHOD[m.group(1)], m.group(2).strip())
    return None


def render(loc: Loc, lang: str = "python") -> str:
    if lang == "java":
        return f"AppiumBy.{JAVA_METHOD_REV[loc.strategy]}({javaq(loc.value)})"
    return f"(AppiumBy.{PY_CONST_REV[loc.strategy]}, {pyq(loc.value)})"


def key(loc: Loc) -> str:
    """Canonical key for baselines (independent of how the test wrote it)."""
    return f"{loc.strategy}={loc.value}"


# ---- evaluation ---------------------------------------------------------------------------------

_UI_CALL = re.compile(r"\.(\w+)\(\s*(\"(?:[^\"\\]|\\.)*\"|true|false|\d+)\s*\)")


def _uiautomator(screen: Screen, sel: str) -> list[Element]:
    inner = re.search(r"scrollIntoView\(\s*(new UiSelector\(\).*)\)\s*;?\s*$", sel, re.S)
    sel = inner.group(1) if inner else sel
    if not sel.strip().startswith("new UiSelector()"):
        raise CannotEvaluate("Only UiSelector expressions can be evaluated offline")
    calls = [(m.group(1), m.group(2)) for m in _UI_CALL.finditer(sel)]
    if not calls:
        raise CannotEvaluate("Empty UiSelector")
    instance = None
    tests = []
    for name, raw in calls:
        arg = raw[1:-1].replace('\\"', '"') if raw.startswith('"') else raw
        field_ = {"text": "text", "description": "acc", "resourceId": "rid", "className": "cls"}
        if name in field_:
            tests.append(lambda e, f=field_[name], a=arg: e.fp.get(f, "") == a)
        elif name in ("textContains", "descriptionContains"):
            f = "text" if name.startswith("text") else "acc"
            tests.append(lambda e, f=f, a=arg: a in e.fp.get(f, ""))
        elif name in ("textStartsWith", "descriptionStartsWith"):
            f = "text" if name.startswith("text") else "acc"
            tests.append(lambda e, f=f, a=arg: e.fp.get(f, "").startswith(a))
        elif name in ("textMatches", "descriptionMatches", "resourceIdMatches", "classNameMatches"):
            f = {"textMatches": "text", "descriptionMatches": "acc", "resourceIdMatches": "rid", "classNameMatches": "cls"}[name]
            rx = re.compile(arg)
            tests.append(lambda e, f=f, rx=rx: bool(rx.fullmatch(e.fp.get(f, ""))))
        elif name in ("clickable", "enabled", "checked", "selected", "scrollable"):
            attr = name
            tests.append(lambda e, attr=attr, a=arg: e.node.get(attr, "false") == a)
        elif name == "instance":
            instance = int(arg)
        else:
            raise CannotEvaluate(f"UiSelector.{name}() can't be evaluated offline")
    hits = [e for e in screen.elements if all(t(e) for t in tests)]
    if instance is not None:
        return hits[instance:instance + 1]
    return hits


_PRED = re.compile(r"""^\s*(\w+)\s*(==|!=|CONTAINS(?:\[c\])?|BEGINSWITH(?:\[c\])?|ENDSWITH(?:\[c\])?|LIKE)\s*(['"])(.*?)\3\s*$""", re.S)
_PRED_ATTR = {"name": "acc", "label": "text", "value": "value", "type": "cls", "identifier": "acc", "placeholderValue": "hint"}


def _predicate(screen: Screen, pred: str) -> list[Element]:
    parts = re.split(r"\s+AND\s+", pred.strip())
    tests = []
    for part in parts:
        m = _PRED.match(part)
        if not m:
            raise CannotEvaluate("Only 'attr == \"value\"' conditions joined by AND can be evaluated offline")
        attr, op, value = m.group(1), m.group(2), m.group(4)
        if attr not in _PRED_ATTR and attr not in ("enabled", "visible"):
            raise CannotEvaluate(f"Predicate attribute '{attr}' can't be evaluated offline")
        ci = op.endswith("[c]")
        op = op.replace("[c]", "")

        def get(e: Element, attr=attr):
            if attr in ("enabled", "visible", "value"):
                return e.node.get(attr, "")
            return e.fp.get(_PRED_ATTR[attr], "")

        def t(e, get=get, op=op, value=value, ci=ci):
            v = get(e)
            v2, val = (v.lower(), value.lower()) if ci else (v, value)
            if op == "==":
                return v2 == val or (attr in ("enabled", "visible") and v2 in ("true", "1") and val in ("1", "true"))
            if op == "!=":
                return v2 != val
            if op == "CONTAINS":
                return val in v2
            if op == "BEGINSWITH":
                return v2.startswith(val)
            if op == "ENDSWITH":
                return v2.endswith(val)
            return bool(re.fullmatch(re.escape(val).replace(r"\*", ".*").replace(r"\?", "."), v2))

        tests.append(t)
    return [e for e in screen.elements if all(t(e) for t in tests)]


def find(screen: Screen, loc: Loc) -> list[Element]:
    """Elements the locator matches on this screen (raises CannotEvaluate for unsupported forms)."""
    s, v = loc.strategy, loc.value
    if s == "accessibility id":
        return [e for e in screen.elements if e.fp.get("acc") == v]
    if s == "id":
        if screen.platform == IOS:
            return [e for e in screen.elements if e.fp.get("acc") == v]
        full = v if ":id/" in v else None
        return [e for e in screen.elements if e.fp.get("rid") and (e.fp["rid"] == v or (not full and e.short_rid == v))]
    if s == "class name":
        return [e for e in screen.elements if e.fp.get("cls") == v]
    if s == "name":
        return [e for e in screen.elements if e.fp.get("acc") == v or (screen.platform == ANDROID and e.fp.get("text") == v)]
    if s == "xpath":
        try:
            nodes = screen.tree.xpath(v)
        except Exception as e:  # noqa: BLE001 - lxml XPath errors
            raise CannotEvaluate(f"Invalid XPath: {e}") from None
        out = []
        for n in nodes if isinstance(nodes, list) else []:
            el = screen.element_for(n)
            if el is not None:
                out.append(el)
        return out
    if s == "-android uiautomator":
        if screen.platform != ANDROID:
            return []
        return _uiautomator(screen, v)
    if s == "-ios predicate string":
        if screen.platform != IOS:
            return []
        return _predicate(screen, v)
    raise CannotEvaluate(f"'{s}' locators can't be evaluated offline")


def count(screen: Screen, loc: Loc) -> int:
    return len(find(screen, loc))


# ---- generating locators ------------------------------------------------------------------------

def _xq(s: str) -> str:
    """XPath string literal."""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in s.split('"')) + ")"


def candidates_for(screen: Screen, el: Element) -> list[Loc]:
    """Locators for one element, most stable first (not yet checked for uniqueness)."""
    fp = el.fp
    out: list[Loc] = []
    if fp.get("acc"):
        out.append(Loc("accessibility id", fp["acc"]))
    if screen.platform == ANDROID:
        if fp.get("rid"):
            out.append(Loc("id", fp["rid"]))
        if fp.get("text"):
            out.append(Loc("-android uiautomator", f'new UiSelector().text({pyq_double(fp["text"])})'))
            out.append(Loc("xpath", f'//{fp["cls"]}[@text={_xq(fp["text"])}]'))
        if fp.get("hint"):
            out.append(Loc("xpath", f'//{fp["cls"]}[@hint={_xq(fp["hint"])}]'))
        if fp.get("rid") and fp.get("text"):
            out.append(Loc("-android uiautomator",
                           f'new UiSelector().resourceId({pyq_double(fp["rid"])}).text({pyq_double(fp["text"])})'))
    else:
        if fp.get("text"):
            out.append(Loc("-ios predicate string", f'label == {pyq_double(fp["text"])}'))
            out.append(Loc("-ios predicate string", f'type == "{fp["cls"]}" AND label == {pyq_double(fp["text"])}'))
            out.append(Loc("xpath", f'//{fp["cls"]}[@label={_xq(fp["text"])}]'))
        if fp.get("hint"):
            out.append(Loc("-ios predicate string", f'placeholderValue == {pyq_double(fp["hint"])}'))
    return out


def pyq_double(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unique_locators(screen: Screen, el: Element) -> list[Loc]:
    out = []
    for loc in candidates_for(screen, el):
        try:
            hits = find(screen, loc)
        except CannotEvaluate:
            continue
        if len(hits) == 1 and hits[0] is el and loc not in out:
            out.append(loc)
    return out


def implied_fingerprint(loc: Loc) -> dict[str, str]:
    """What a locator says about its element, when there's no baseline."""
    s, v = loc.strategy, loc.value
    if s == "accessibility id":
        return {"acc": v}
    if s == "id":
        return {"rid": v, "acc": v}
    if s == "class name":
        return {"cls": v}
    if s == "name":
        return {"acc": v, "text": v}
    fp: dict[str, str] = {}
    for attr, f in (("text", "text"), ("content-desc", "acc"), ("resource-id", "rid"), ("label", "text"), ("name", "acc"),
                    ("hint", "hint")):
        m = re.search(rf"@{attr}\s*=\s*['\"]([^'\"]+)['\"]", v)
        if m:
            fp.setdefault(f, m.group(1))
    m = re.match(r"^//([\w.]+)", v)
    if m and m.group(1) != "*":
        fp["cls"] = m.group(1)
    for name, f in (("text", "text"), ("description", "acc"), ("resourceId", "rid"), ("className", "cls")):
        m = re.search(rf"\.{name}\(\s*\"([^\"]+)\"\s*\)", v)
        if m:
            fp.setdefault(f, m.group(1))
    for attr, f in (("label", "text"), ("name", "acc"), ("type", "cls"), ("placeholderValue", "hint")):
        m = re.search(rf"\b{attr}\s*==\s*['\"]([^'\"]+)['\"]", v)
        if m:
            fp.setdefault(f, m.group(1))
    return fp
