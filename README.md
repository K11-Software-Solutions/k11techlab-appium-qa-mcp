# k11 Appium QA: self-healing locators for mobile apps

<!-- mcp-name: io.github.K11-Software-Solutions/appium-qa -->

[![PyPI](https://img.shields.io/pypi/v/k11-appium-qa-mcp?label=PyPI)](https://pypi.org/project/k11-appium-qa-mcp/)
[![CI](https://github.com/K11-Software-Solutions/k11techlab-appium-qa-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/K11-Software-Solutions/k11techlab-appium-qa-mcp/actions/workflows/ci.yml)
[![E2E Android](https://github.com/K11-Software-Solutions/k11techlab-appium-qa-mcp/actions/workflows/e2e-android.yml/badge.svg)](https://github.com/K11-Software-Solutions/k11techlab-appium-qa-mcp/actions/workflows/e2e-android.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**When an app redesign breaks your Appium tests, fix the locators with evidence, not guesses.**

Self-healing for mobile tests usually lives inside a paid device cloud, as a black box. k11 Appium QA is open source and runs wherever your tests already run: a laptop with an emulator, your CI, or any device cloud.

- **Heals on the exact screen the test failed on.** A small pytest plugin saves the screen (Appium's page source) at the moment an element isn't found. Healing runs against that screen, with no need to navigate back to it.
- **Shows its working:** confidence, the reasons ("text equivalent: Log in → Sign in", "resource id similar 62%"), the runner-up and alternatives. Accessibility ids are preferred.
- **Fixes locators where they live:** Python `AppiumBy`/`By` tuples and Page Object constants; Java `AppiumBy`/`MobileBy`/`By` calls and `@AndroidFindBy`/`@iOSXCUITFindBy` annotations (each keeps its own platform).
- **Verified, not hoped:** `heal-ci` re-runs the healed tests on the device and reverts any change that doesn't pass. When a test gets further and breaks on the next locator, it heals that one too.
- **Android and iOS:** UiAutomator2 and XCUITest screens; accessibility id, id, class name, XPath, UiSelector and iOS predicate locators are evaluated offline.
- **Accessibility check:** tappable elements a screen reader can't name, and duplicate accessibility ids. Bad for users, and the reason tests are fragile.

## See it heal

A login screen redesign broke three locators in one test and one in another. `k11-appium-qa-mcp heal-ci --apply`:

> **3 healed and verified**, 1 needs review, 1 failure not caused by locators.
>
> | Test | Old locator | New locator | Confidence | Re-run |
> |---|---|---|---|---|
> | test_log_in | `(AppiumBy.ID, "et_email")` | `(AppiumBy.ACCESSIBILITY_ID, "email-field")` | 94% | passed |
> | test_log_in | `(AppiumBy.ACCESSIBILITY_ID, "password-input")` | `(AppiumBy.ACCESSIBILITY_ID, "password-field")` | 100% | passed |
> | test_log_in | `(AppiumBy.ACCESSIBILITY_ID, "login-button")` | `(AppiumBy.ACCESSIBILITY_ID, "sign-in-button")` | 77% | passed |
>
> *Why "sign-in-button"?* accessibility id similar (62%) · resource id similar: `btn_login` → `btn_sign_in` · text equivalent: "Log in" → "Sign in" · class matches.
>
> **Needs a person:** the sign-up button lost its accessibility id; best guess `btn_register` at 42%, so it was not changed.
> **Not caused by locators:** the title changed from "Welcome back". Untouched; it may be a real change.

The changes land in the Page Object (`tests/pages/login_page.py`), not scattered across tests.

## Tools (MCP)

Every tool works on a **saved screen** (page source XML, e.g. the one saved when a test failed) or on the **live device** when `.qa-mcp/appium.yaml` points at an Appium server.

| Tool | What it does |
|---|---|
| `inspect_screen` | Elements with locators verified to match exactly one element, most stable first |
| `check_locator` | How many elements a locator matches (1 = good) and whether it's visible |
| `capture_baseline` | While tests pass, records each locator's element (per platform) so healing is confident later |
| `heal_locator` | Finds the intended element and suggests a verified replacement, in Python or Java form |
| `accessibility_check` | Unnamed tappable elements and duplicate accessibility ids |
| `save_screen` | Saves the live device's screen for later, device-free work |
| `run_tests` | Runs the pytest suite; failures include the locator, line, platform and saved screen |

Command: **`/heal-mobile-tests`**, a guided workflow from failing tests to healed, verified locators.

k11 Appium QA doesn't try to replace device-control servers like the official [Appium MCP](https://github.com/appium/appium-mcp) or [mobile-mcp](https://github.com/mobile-next/mobile-mcp); use them to drive and explore the app, and this to keep your existing test suite healthy.

## Install

```bash
pip install "k11-appium-qa-mcp[live]"     # [live] adds the Appium client for live sessions; saved screens need nothing extra
```

Claude Code plugin:

```text
/plugin marketplace add K11-Software-Solutions/k11techlab-appium-qa-mcp
/plugin install appium-qa@k11-appium-qa
```

Any MCP client:

```json
{ "mcpServers": { "appium-qa": { "command": "uvx", "args": ["k11-appium-qa-mcp"] } } }
```

For live sessions, copy [`docs/appium.example.yaml`](docs/appium.example.yaml) to `.qa-mcp/appium.yaml` (server URL and capabilities; secrets as `${ENV:NAME}`). Device clouds such as BrowserStack and Sauce Labs work the same way.

## Healing in CI

```bash
k11-appium-qa-mcp heal-ci            # dry run: suggestions only
k11-appium-qa-mcp heal-ci --apply    # apply confident changes, verify on the device, revert what fails
```

1. Runs your pytest suite with the plugin, which saves the screen of every test that fails on a missing element.
2. Heals each broken locator against that screen and the committed baseline (`.qa-mcp/appium-baselines.json`).
3. Applies a change only at **70%** confidence or more (`--min-confidence`), clearly ahead of the runner-up. Tests that share a broken locator are healed together, with one change.
4. Re-runs the healed tests on the device; a test that now breaks on another locator gets that one healed too (up to five rounds). Every change is kept only if all the tests using it pass.
5. Leaves failures that aren't about locators alone, and writes `heal-report.md` (for the pull request) and `heal-report.json`.

[`docs/heal-mobile-locators.yml`](docs/heal-mobile-locators.yml) runs it on an Android emulator in GitHub Actions and opens a pull request. The report uses the same format as k11 Playwright QA and Selenium QA, so the [k11 Release Gate](https://github.com/K11-Software-Solutions/k11techlab-release-gate-mcp) reads it as UI evidence.

**Java projects:** the MCP tools and source rewriting support Java (Page Objects with `@AndroidFindBy`/`@iOSXCUITFindBy`, `AppiumBy` calls). `heal-ci` currently runs pytest suites; for Maven or Gradle suites, save the screen on failure (`driver.getPageSource()`) and use `heal_locator`, or `/heal-mobile-tests`.

## How it's tested

- **No device needed for most tests:** real-format UiAutomator2 and XCUITest page sources, and a fake Appium driver that serves them, run the whole heal-ci loop (run, heal, rewrite, re-run, report) in seconds.
- **A real emulator on every push:** [`e2e-android.yml`](.github/workflows/e2e-android.yml) starts an Android 14 emulator and an Appium server, reads the built-in Settings app live, captures a baseline and heals a broken locator, both live and from the saved screen.

## Safety

- Suggestions are never applied silently: `heal_locator` never edits files, and `heal-ci` changes code only above the threshold and keeps a change only when the tests pass.
- Screens are parsed with entity expansion and network access disabled; paths must stay inside the project.
- Locators from tests and models are parsed, never executed.
- Capabilities and credentials come from `.qa-mcp/appium.yaml` with `${ENV:NAME}` secrets.

## Part of the K11 QA suite

[Playwright QA](https://github.com/K11-Software-Solutions/k11techlab-playwright-qa-mcp) · [Selenium QA](https://github.com/K11-Software-Solutions/k11techlab-selenium-qa-mcp) · **Appium QA** · [API Contract QA](https://github.com/K11-Software-Solutions/k11techlab-api-contract-qa-mcp) · [API Functional QA](https://github.com/K11-Software-Solutions/k11techlab-api-functional-qa-mcp) · [Performance QA](https://github.com/K11-Software-Solutions/k11techlab-performance-qa-mcp) · [LLM Assure](https://github.com/K11-Software-Solutions/k11techlab-llm-assurance-toolkit) · [Release Gate](https://github.com/K11-Software-Solutions/k11techlab-release-gate-mcp) · [Release Readiness Agent](https://github.com/K11-Software-Solutions/k11techlab-release-readiness-agent)

## Roadmap

- `heal-ci` for Java (Maven/Gradle) suites, with a TestNG/JUnit listener that saves the screen on failure
- iOS simulator job in CI (macOS runner)
- Screenshot similarity as an extra healing signal
- Flutter and React Native specifics (semantics labels, testID)

---

Built by [K11 Software Solutions](https://k11softwaresolutions.com). MIT licensed.
