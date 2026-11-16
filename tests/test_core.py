"""Screens, locators, healing, rewriting and accessibility checks on real-format page sources."""

from pathlib import Path

import pytest

from k11_appium_qa import heal as H
from k11_appium_qa import locators as L
from k11_appium_qa.rewrite import CannotRewrite, replace_java, replace_python
from k11_appium_qa.screen import ANDROID, IOS, accessibility_issues, load, parse

SCREENS = Path(__file__).parent / "fixtures" / "screens"


@pytest.fixture(scope="module")
def v1():
    return load(SCREENS / "android_login_v1.xml")


@pytest.fixture(scope="module")
def v2():
    return load(SCREENS / "android_login_v2.xml")


@pytest.fixture(scope="module")
def ios():
    return load(SCREENS / "ios_login_v2.xml")


def test_platform_and_fingerprints(v1, ios):
    assert v1.platform == ANDROID and v1.package == "com.k11.shop"
    btn = next(e for e in v1.elements if e.fp.get("acc") == "login-button")
    assert btn.fp == {"cls": "android.widget.Button", "acc": "login-button", "rid": "com.k11.shop:id/btn_login", "text": "Log in"}
    assert btn.clickable and btn.visible and btn.short_rid == "btn_login"
    assert ios.platform == IOS
    field = next(e for e in ios.elements if e.fp.get("acc") == "email-field")
    assert field.fp["hint"] == "Email address" and field.clickable


@pytest.mark.parametrize("text,strategy,value", [
    ('(AppiumBy.ACCESSIBILITY_ID, "login-button")', "accessibility id", "login-button"),
    ("AppiumBy.ID, value='btn_login'", "id", "btn_login"),
    ('(By.XPATH, "//android.widget.Button")', "xpath", "//android.widget.Button"),
    ('("accessibility id", "login-button")', "accessibility id", "login-button"),
    ('AppiumBy.accessibilityId("login-button")', "accessibility id", "login-button"),
    ('MobileBy.AccessibilityId("login-button");', "accessibility id", "login-button"),
    ('@AndroidFindBy(uiAutomator = "new UiSelector().text(\\"Log in\\")")', "-android uiautomator", 'new UiSelector().text("Log in")'),
    ('@iOSXCUITFindBy(iOSNsPredicate = "label == \\"Sign in\\"")', "-ios predicate string", 'label == "Sign in"'),
    ("By.id: btn_login", "id", "btn_login"),
])
def test_parse_every_form(text, strategy, value):
    assert L.parse(text) == L.Loc(strategy, value)


def test_parse_rejects_code(v1):
    assert L.parse('(AppiumBy.ID, __import__("os").getcwd())') is None
    assert L.parse("driver.quit()") is None


@pytest.mark.parametrize("loc,n1,n2", [
    (L.Loc("accessibility id", "login-button"), 1, 0),
    (L.Loc("id", "btn_login"), 1, 0),
    (L.Loc("id", "com.k11.shop:id/forgot"), 1, 1),
    (L.Loc("class name", "android.widget.EditText"), 2, 2),
    (L.Loc("xpath", '//android.widget.Button[@text="Create account"]'), 1, 1),
    (L.Loc("-android uiautomator", 'new UiSelector().textContains("password")'), 1, 1),
    (L.Loc("-android uiautomator", 'new UiSelector().className("android.widget.Button").instance(1)'), 1, 1),
    (L.Loc("-android uiautomator", 'new UiScrollable(new UiSelector().scrollable(true)).scrollIntoView(new UiSelector().text("Create account"))'), 1, 1),
])
def test_offline_evaluation(v1, v2, loc, n1, n2):
    assert (L.count(v1, loc), L.count(v2, loc)) == (n1, n2)


def test_ios_predicates_and_unsupported_forms(ios, v1):
    assert L.count(ios, L.Loc("-ios predicate string", 'type == "XCUIElementTypeButton" AND label == "Sign in"')) == 1
    assert L.count(ios, L.Loc("-ios predicate string", "label BEGINSWITH[c] 'forgot'")) == 1
    assert L.count(ios, L.Loc("id", "sign-in-button")) == 1                 # iOS id is the name
    assert L.count(v1, L.Loc("-ios predicate string", 'label == "x"')) == 0  # wrong platform
    with pytest.raises(L.CannotEvaluate):
        L.count(ios, L.Loc("-ios class chain", "**/XCUIElementTypeButton"))
    with pytest.raises(L.CannotEvaluate):
        L.count(v1, L.Loc("-android uiautomator", "new UiSelector().childSelector(new UiSelector())"))


def test_unique_locators_prefer_accessibility_ids(v2, ios):
    btn = next(e for e in v2.elements if e.fp.get("acc") == "sign-in-button")
    assert [L.render(x) for x in L.unique_locators(v2, btn)][:2] == [
        '(AppiumBy.ACCESSIBILITY_ID, "sign-in-button")', '(AppiumBy.ID, "com.k11.shop:id/btn_sign_in")']
    create = next(e for e in ios.elements if e.fp.get("text") == "Create account")
    assert L.render(L.unique_locators(ios, create)[0]) == "(AppiumBy.IOS_PREDICATE, 'label == \"Create account\"')"


def test_heal_with_baseline_and_runner_up(v1, v2):
    base = H.fingerprint_of(next(e for e in v1.elements if e.fp.get("acc") == "login-button"))
    r = H.heal(v2, L.Loc("accessibility id", "login-button"), "login", base)
    assert r["status"] == "healed" and r["suggestion"] == '(AppiumBy.ACCESSIBILITY_ID, "sign-in-button")'
    assert r["confidence"] >= 0.7 and any("text equivalent" in x for x in r["reasons"])
    assert r["alternatives"] and r["candidates"][1]["score"] < r["confidence"]
    assert H.heal(v1, L.Loc("accessibility id", "login-button"), "x")["status"] == "healthy"
    assert H.heal(v2, None, "x")["status"] == "unparseable"
    gone = H.heal(v2, L.Loc("accessibility id", "wishlist-heart"), "x", {"acc": "wishlist-heart", "cls": "android.widget.ToggleButton"})
    assert gone["status"] in ("not_found", "needs_review")


def test_heal_renders_java(v1, v2):
    base = H.fingerprint_of(next(e for e in v1.elements if e.fp.get("acc") == "login-button"))
    r = H.heal(v2, L.Loc("accessibility id", "login-button"), "x", base, lang="java")
    assert r["suggestion"] == 'AppiumBy.accessibilityId("sign-in-button")'


def test_rewrite_python_page_object_and_import():
    src = 'from selenium.webdriver.common.by import By\n\nLOGIN = (By.ID, "btn_login")  # (By.ID, "btn_login")\nX = driver.find_element(by=By.ID, value="btn_login")\n'
    out, reps = replace_python(src, L.Loc("id", "btn_login"), L.Loc("accessibility id", "sign-in-button"), "p.py")
    assert 'LOGIN = (AppiumBy.ACCESSIBILITY_ID, "sign-in-button")  # (By.ID, "btn_login")' in out
    assert 'find_element(by=AppiumBy.ACCESSIBILITY_ID, value="sign-in-button")' in out
    assert "from appium.webdriver.common.appiumby import AppiumBy\n" in out and [r.line for r in reps] == [3, 4]


def test_rewrite_java_annotations_keep_their_platform():
    src = ('package a;\n\nimport io.appium.java_client.pagefactory.AndroidFindBy;\n\nclass P {\n'
           '  @AndroidFindBy(id = "btn_login") WebElement a;\n  @iOSXCUITFindBy(accessibility = "login-button") WebElement b;\n'
           '  void t() { d.findElement(By.id("btn_login")); }\n}\n')
    out, reps = replace_java(src, L.Loc("id", "btn_login"), L.Loc("accessibility id", "sign-in-button"), "P.java")
    assert '@AndroidFindBy(accessibility = "sign-in-button")' in out and '@iOSXCUITFindBy(accessibility = "login-button")' in out
    assert 'd.findElement(AppiumBy.accessibilityId("sign-in-button"))' in out
    assert out.index("import io.appium.java_client.AppiumBy;") < out.index("import io.appium.java_client.pagefactory")
    with pytest.raises(CannotRewrite):
        replace_java(src, L.Loc("id", "btn_login"), L.Loc("-ios predicate string", "label == 'x'"), "P.java")


def test_accessibility_issues(v1, v2):
    assert accessibility_issues(v1) == []
    issues = accessibility_issues(v2)
    assert [i["issue"] for i in issues] == ["no accessible name"] and issues[0]["rid"] == "com.k11.shop:id/btn_help"
    dup = parse('<hierarchy><android.widget.Button content-desc="ok" clickable="true" bounds="[0,0][9,9]"/>'
                '<android.widget.Button content-desc="ok" clickable="true" bounds="[0,10][9,19]"/></hierarchy>')
    assert accessibility_issues(dup)[0]["issue"] == "duplicate accessibility id"


def test_bad_page_source():
    with pytest.raises(ValueError):
        parse("<html><body>not a screen")
    with pytest.raises(ValueError):
        parse('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><hierarchy>&e;</hierarchy>')
