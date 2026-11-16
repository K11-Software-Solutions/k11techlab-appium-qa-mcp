# Changelog

## 0.1.0 (16 November 2026)

- First release: self-healing Appium locators for native and hybrid Android and iOS apps.
- Offline evaluation of accessibility id, id, class name, XPath, common UiSelector and iOS predicate locators against Appium page source.
- Healing against the screen a test failed on (saved by the pytest plugin), with baselines per platform, confidence, reasons, runner-up and alternatives.
- Rewrites Python (`AppiumBy`/`By` tuples, keyword form) and Java (`AppiumBy`/`MobileBy`/`By` calls, `@AndroidFindBy`, `@iOSXCUITFindBy`).
- `heal-ci`: heals tests that share a broken locator together, keeps healing when a test gets further and breaks on the next locator, verifies on the device, reverts what doesn't pass. Report format read by k11 Release Gate.
- Accessibility check: tappable elements without an accessible name, duplicate accessibility ids.
- MCP tools on saved screens or a live device (Appium server); `/heal-mobile-tests` command.
