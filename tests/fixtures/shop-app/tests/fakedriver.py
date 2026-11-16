"""A stand-in for an Appium session that answers from a saved screen (page source XML).

Lets the suite (and k11 Appium QA's heal-ci) run without a device: element lookups,
taps, typing and the page source behave like a real UiAutomator2 session.
"""
from appium.webdriver.locator_converter import AppiumLocatorConverter
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.command import Command

from k11_appium_qa.locators import Loc, find
from k11_appium_qa.screen import parse


class FakeAppiumDriver(WebDriver):
    def __init__(self, screen_path: str, platform: str = "Android"):  # noqa: D107 - no remote session
        with open(screen_path, encoding="utf8") as fh:
            self._xml = fh.read()
        self._screen = parse(self._xml)
        self.session_id = None
        self.locator_converter = AppiumLocatorConverter()
        self.caps = {"platformName": platform}
        self._els = {}
        self.typed = {}

    def quit(self):
        pass

    def execute(self, command, params=None):
        params = params or {}
        if command in (Command.FIND_ELEMENT, Command.FIND_ELEMENTS):
            hits = find(self._screen, Loc(params["using"], params["value"]))
            els = []
            for h in hits:
                eid = str(id(h.node))
                self._els[eid] = h
                els.append(WebElement(self, eid))
            if command == Command.FIND_ELEMENT:
                if not els:
                    raise NoSuchElementException(
                        f"An element could not be located on the page using the given search parameters. ({params['using']}={params['value']})")
                return {"value": els[0]}
            return {"value": els}
        if command == Command.GET_PAGE_SOURCE:
            return {"value": self._xml}
        if command == Command.GET_ELEMENT_TEXT:
            return {"value": self._els[params["id"]].fp.get("text", "")}
        if command == Command.SEND_KEYS_TO_ELEMENT:
            self.typed[params["id"]] = params.get("text", "")
            return {"value": None}
        if command in (Command.CLICK_ELEMENT, Command.CLEAR_ELEMENT):
            return {"value": None}
        raise NotImplementedError(command)
