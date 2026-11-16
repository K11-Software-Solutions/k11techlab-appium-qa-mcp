---
description: Run the Appium suite and repair locators broken by an app redesign, on the screen each test failed on
argument-hint: "[test path or -k expression]"
---

Repair Appium tests whose locators broke because the app's UI changed, using the appium-qa MCP tools.

Input: $ARGUMENTS (optional test path or keyword)

1. Call `run_tests` (with the given path or keyword, if any). Failures caused by a missing element come with the locator, the line, the platform and the screen saved when the test failed.
2. For each such failure, call `heal_locator` with the locator and that `screen`. Use `lang: "java"` for Java projects.
3. Act on the result:
   - `healed`: replace the old locator with `suggestion` where it is defined (often a Page Object constant or an `@AndroidFindBy`/`@iOSXCUITFindBy` annotation; keep the annotation's platform).
   - `needs_review` or `ambiguous`: show the candidates, scores and reasons, and ask which one is right before editing.
   - `not_found`: the element may have been removed. Report it; don't invent a locator.
4. Leave failures that aren't about locators untouched and list them separately; they may be real bugs.
5. Re-run the affected tests on the device. When they pass, call `capture_baseline` on the screens they use so future redesigns heal with confidence.
6. Mention any `accessibility_check` findings on those screens: elements without an accessible name are hard for users and for tests.

Finish with a table: test, old locator, new locator, confidence, and status. Never weaken an assertion to make a test pass.
