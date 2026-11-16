from appium.webdriver.common.appiumby import AppiumBy
from pages.login_page import LoginPage


def test_log_in(driver):
    LoginPage(driver).log_in("ana@example.com", "secret")


def test_open_sign_up(driver):
    driver.find_element(*LoginPage.SIGN_UP).click()


def test_forgot_password(driver):
    driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Forgot password?")').click()


def test_title(driver):
    assert LoginPage(driver).title() == "Welcome back"
