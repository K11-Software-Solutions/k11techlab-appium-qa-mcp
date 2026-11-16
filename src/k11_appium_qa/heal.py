"""Self-healing mobile locators.

A broken locator's element is found again by scoring every visible element on the screen
against its last known fingerprint (a baseline captured while the test passed, or what the
locator itself implies): accessibility id, resource id, text, hint and class. The best match
gets a replacement locator verified unique on that screen, preferring accessibility ids.
Suggestions are never applied silently.
"""

from __future__ import annotations

import re
from typing import Any

from .locators import CannotEvaluate, Loc, find, implied_fingerprint, render, unique_locators
from .screen import Element, Screen

SYNONYMS = {
    "btn": "button", "pwd": "password", "passwd": "password", "pass": "password", "signin": "login",
    "logon": "login", "login": "login", "log": "login", "sign": "login", "mail": "email", "e-mail": "email",
    "usr": "user", "username": "user", "submit": "send", "qty": "quantity", "del": "delete",
    "remove": "delete", "nav": "navigation", "img": "image", "txt": "text", "lbl": "label", "et": "edit",
    "tv": "text", "iv": "image", "fab": "button", "cta": "button", "checkout": "checkout", "cart": "cart",
}
# id prefixes that carry no meaning (btn_login, et_email, tvTitle)
_NOISE = {"button", "edit", "text", "image", "label", "view", "field", "input"}


def tokens(s: str | None) -> list[str]:
    if not s:
        return []
    s = s.split(":id/", 1)[-1]
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower()
    return [SYNONYMS.get(t, t) for t in re.split(r"[^a-z0-9]+", s) if t]


def _levenshtein(a: str, b: str) -> int:
    dp = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, len(b) + 1):
            tmp = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (0 if a[i - 1] == b[j - 1] else 1))
            prev = tmp
    return dp[len(b)]


def text_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    ta = [t for t in dict.fromkeys(tokens(a))]
    tb = [t for t in dict.fromkeys(tokens(b))]
    ca, cb = [t for t in ta if t not in _NOISE] or ta, [t for t in tb if t not in _NOISE] or tb
    inter = len(set(ca) & set(cb))
    union = len(set(ca) | set(cb)) or 1
    x, y = " ".join(ca), " ".join(cb)
    lev = 1 - _levenshtein(x, y) / max(len(x), len(y), 1)
    return max(inter / union, lev)


WEIGHTS: list[tuple[str, float, str]] = [
    ("acc", 3, "fuzzy"),
    ("rid", 2.5, "fuzzy"),
    ("text", 2, "fuzzy"),
    ("hint", 1, "fuzzy"),
    ("cls", 1.5, "exact"),
]
LABEL = {"acc": "accessibility id", "rid": "resource id", "text": "text", "hint": "hint", "cls": "class"}
AUTO_SCORE = 0.55
AUTO_MARGIN = 0.1


def score(ref: dict[str, str], el: Element) -> tuple[float, list[str]]:
    total = got = 0.0
    reasons: list[str] = []
    for k, w, mode in WEIGHTS:
        r = ref.get(k)
        if not r:
            continue
        total += w
        v = el.fp.get(k)
        s = (1.0 if v is not None and v.lower() == r.lower() else 0.0) if mode == "exact" else text_similarity(r, v)
        got += w * s
        name = LABEL[k]
        if s >= 0.99:
            reasons.append(f"{name} matches" if v == r else f'{name} equivalent: "{r}" → "{v}"')
        elif s >= 0.4:
            reasons.append(f'{name} similar ({round(s * 100)}%): "{r}" → "{v}"')
        elif v is not None and mode == "exact":
            reasons.append(f'{name} differs: "{r}" → "{v}"')
    return (got / total if total else 0.0), reasons


def fingerprint_of(el: Element) -> dict[str, str]:
    return {k: v for k, v in el.fp.items() if k in LABEL}


def heal(screen: Screen, loc: Loc | None, original: str, baseline: dict[str, str] | None = None,
         lang: str = "python") -> dict[str, Any]:
    if loc is None:
        return {"status": "unparseable", "original": original,
                "note": 'Use one Appium locator, e.g. (AppiumBy.ACCESSIBILITY_ID, "Login"), AppiumBy.id("login") or @AndroidFindBy(id = "login").'}
    try:
        matches = len(find(screen, loc))
    except CannotEvaluate as e:
        matches = None
        why = str(e)
    else:
        why = ""
    if matches == 1:
        return {"status": "healthy", "original": render(loc, lang), "matches": 1}
    ref = baseline or implied_fingerprint(loc)
    if not ref:
        return {"status": "needs_review", "original": render(loc, lang), "note": "Nothing to match against: capture a baseline while the test passes."}
    ranked = sorted(((e, *score(ref, e)) for e in screen.elements if e.visible), key=lambda x: x[1], reverse=True)[:3]
    cands, alternatives = [], []
    for i, (el, sc, reasons) in enumerate(ranked):
        uniq = unique_locators(screen, el)
        if i == 0:
            alternatives = [render(u, lang) for u in uniq[1:]]
        cands.append({"locator": render(uniq[0], lang) if uniq else None, "score": round(sc, 2), "reasons": reasons,
                      "element": {k: v for k, v in el.fp.items()}})
    base: dict[str, Any] = {"status": "needs_review", "original": render(loc, lang), "platform": screen.platform,
                            "matches": matches, "candidates": cands, "referenceSource": "baseline" if baseline else "implied"}
    if why:
        base["evaluation"] = why
    if alternatives:
        base["alternatives"] = alternatives
    best = cands[0] if cands else None
    if not best or best["score"] < 0.3:
        return {**base, "status": "not_found", "note": "No element on the screen resembles the original."}
    margin = best["score"] - cands[1]["score"] if len(cands) > 1 else best["score"]
    if matches and matches > 1:
        base["status"] = "ambiguous"
    if best["locator"] and best["score"] >= AUTO_SCORE and margin >= AUTO_MARGIN and matches is not None:
        return {**base, "status": "ambiguous" if matches > 1 else "healed", "suggestion": best["locator"],
                "confidence": best["score"], "reasons": best["reasons"]}
    note = ("The original locator can't be evaluated offline, so a person should confirm." if matches is None else
            "No baseline fingerprint for this locator, so matching used only what the locator itself implies. "
            "Run capture_baseline while tests pass to make healing more reliable." if not baseline else
            "Best match is not clearly better than the alternatives; confirm before applying.")
    return {**base, "suggestion": best["locator"], "confidence": best["score"], "reasons": best["reasons"], "note": note}
