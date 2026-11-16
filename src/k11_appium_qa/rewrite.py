"""Rewrite an Appium locator in source code, touching nothing else.

Python: ``AppiumBy.X, "v"`` / ``By.X, "v"`` (also ``by=AppiumBy.X, value="v"``) wherever it
appears: find_element calls, Page Object constants, expected conditions. Comments are skipped.
Adds ``from appium.webdriver.common.appiumby import AppiumBy`` if the file doesn't have it.

Java: ``AppiumBy.accessibilityId("v")``-style calls (also ``MobileBy`` and ``By``), and
``@AndroidFindBy(...)`` / ``@iOSXCUITFindBy(...)`` annotations; the annotation keeps its
platform and gets the right key (``accessibility = "v"``), or the change is refused when
that annotation has no key for the new strategy.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from .locators import (ANDROID_KEYS, IOS_KEYS, JAVA_METHOD, JSTR, PY_CONST, PY_CONST_REV, Loc, java_unescape, javaq,
                       parse_annotation_args, pyq, render)

SKIP_DIRS = {".venv", "venv", "env", ".git", "node_modules", "__pycache__", ".qa-mcp", ".tox", ".nox",
             "site-packages", "build", "dist", ".pytest_cache", ".mypy_cache", "target", ".gradle", ".idea", "out"}
APPIUMBY_IMPORT = "from appium.webdriver.common.appiumby import AppiumBy"


@dataclass
class Replacement:
    file: str
    line: int
    before: str
    after: str


class CannotRewrite(ValueError):
    pass


# ---- Python -------------------------------------------------------------------------------------

def _py_spans(source: str, target: Loc) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(source).readline)
                if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    spans = []
    for i in range(len(toks) - 4):
        a, dot, const, comma = toks[i:i + 4]
        if not (a.string in ("AppiumBy", "MobileBy", "By") and dot.string == "." and const.string in PY_CONST and comma.string == ","):
            continue
        j = i + 4
        if j + 2 < len(toks) and toks[j].string == "value" and toks[j + 1].string == "=":
            j += 2
        s = toks[j] if j < len(toks) else None
        if s is None or s.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(s.string)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str) and Loc(PY_CONST[const.string], value) == target:
            spans.append((a.start, s.end))
    return spans


def _offset(lines: list[str], pos: tuple[int, int]) -> int:
    row, col = pos
    return sum(len(line) for line in lines[: row - 1]) + col


def _ensure_import(source: str) -> str:
    if re.search(r"^\s*from\s+appium\.webdriver\.common\.appiumby\s+import\s+.*\bAppiumBy\b", source, re.M):
        return source
    lines = source.splitlines(keepends=True)
    last = -1
    for i, ln in enumerate(lines):
        if re.match(r"^(from\s+\S+\s+import\s|import\s)", ln):
            last = i
        elif last >= 0 and ln.strip() and not ln.startswith((" ", "\t", ")")):
            break
    nl = "\n"
    if last < 0:
        return APPIUMBY_IMPORT + nl + source
    # skip a parenthesised multi-line import
    while last + 1 < len(lines) and "(" in lines[last] and ")" not in "".join(lines[last:last + 1]):
        last += 1
        if ")" in lines[last]:
            break
    return "".join(lines[: last + 1]) + APPIUMBY_IMPORT + nl + "".join(lines[last + 1:])


def replace_python(source: str, broken: Loc, healed: Loc, file: str = "") -> tuple[str, list[Replacement]]:
    spans = _py_spans(source, broken)
    if not spans:
        return source, []
    lines = source.splitlines(keepends=True)
    out, reps = source, []
    for start, end in reversed(spans):
        a, b = _offset(lines, start), _offset(lines, end)
        before = out[a:b]
        kw = "value=" if re.search(r",\s*value\s*=", before) else ""
        new = f"AppiumBy.{PY_CONST_REV[healed.strategy]}, {kw}{pyq(healed.value)}"
        reps.append(Replacement(file, start[0], before, new))
        out = out[:a] + new + out[b:]
    reps.reverse()
    return _ensure_import(out), reps


# ---- Java ---------------------------------------------------------------------------------------

def _mask_java(src: str) -> str:
    """Comments blanked (same length, newlines kept); string literals kept."""
    out, i, n = list(src), 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = [" "] * (j - i)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [c if c == "\n" else " " for c in src[i:j]]
            i = j
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _close_paren(text: str, open_idx: int) -> int:
    depth, i = 0, open_idx
    while i < len(text):
        c = text[i]
        if c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


_JCALL = re.compile(rf"\b(AppiumBy|MobileBy|By)\s*\.\s*(\w+)\s*\(\s*({JSTR})\s*\)")
_JANNO = re.compile(r"@(AndroidFindBy|iOSXCUITFindBy)\s*\(")


def _java_spans(src: str, target: Loc) -> list[tuple[int, int, str, str]]:
    masked = _mask_java(src)
    spans = []
    for m in _JCALL.finditer(masked):
        if m.group(2) in JAVA_METHOD:
            v = java_unescape(m.group(3))
            if v and Loc(JAVA_METHOD[m.group(2)], v) == target:
                spans.append((m.start(), m.end(), "call", m.group(1)))
    for m in _JANNO.finditer(masked):
        close = _close_paren(masked, m.end() - 1)
        if close > 0 and parse_annotation_args(m.group(1), masked[m.end():close]) == target:
            spans.append((m.start(), close + 1, "anno", m.group(1)))
    return sorted(spans)


def replace_java(source: str, broken: Loc, healed: Loc, file: str = "") -> tuple[str, list[Replacement]]:
    out, reps = source, []
    for start, end, kind, owner in reversed(_java_spans(source, broken)):
        if kind == "call":
            new = render(healed, "java")
            if owner == "By" and healed.strategy in ("id", "xpath", "class name", "name"):
                new = new.replace("AppiumBy.", "By.", 1)
        else:
            keys = {v: k for k, v in (ANDROID_KEYS if owner == "AndroidFindBy" else IOS_KEYS).items()}
            if healed.strategy not in keys:
                raise CannotRewrite(f"@{owner} has no key for a '{healed.strategy}' locator")
            new = f"@{owner}({keys[healed.strategy]} = {javaq(healed.value)})"
        reps.append(Replacement(file, source.count("\n", 0, start) + 1, source[start:end], new))
        out = out[:start] + new + out[end:]
    reps.reverse()
    if reps and "AppiumBy." in out and not re.search(r"^\s*import\s+io\.appium\.java_client\.(AppiumBy|\*)\s*;", out, re.M):
        imp = "import io.appium.java_client.AppiumBy;\n"
        first_import = re.search(r"^import\s", out, re.M)
        pkg = re.search(r"^package\s+[\w.]+\s*;[ \t]*\n", out, re.M)
        if first_import:
            out = out[:first_import.start()] + imp + out[first_import.start():]
        elif pkg:
            out = out[:pkg.end()] + "\n" + imp + out[pkg.end():]
        else:
            out = imp + out
    return out, reps


def replace_locator(source: str, broken: Loc, healed: Loc, file: str = "") -> tuple[str, list[Replacement]]:
    return replace_java(source, broken, healed, file) if file.endswith(".java") else replace_python(source, broken, healed, file)


# ---- project search -----------------------------------------------------------------------------

def source_files(root: Path):
    root = Path(root)
    for pattern in ("*.py", "*.java"):
        for p in root.rglob(pattern):
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS or part.startswith(".tmp") for part in rel.parts[:-1]):
                continue
            yield p


def contains_locator(path: Path, broken: Loc) -> bool:
    text = path.read_text("utf8", errors="replace")
    return bool(_java_spans(text, broken) if path.suffix == ".java" else _py_spans(text, broken))


def locate_in_project(root: Path, broken: Loc, prefer: Path | None = None) -> list[Path]:
    found = [p for p in source_files(root) if contains_locator(p, broken)]
    if prefer:
        found.sort(key=lambda p: 0 if p.resolve() == Path(prefer).resolve() else 1)
    return found
