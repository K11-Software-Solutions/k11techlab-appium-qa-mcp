from appium.webdriver.common.appiumby import AppiumBy


class LoginPage:
    EMAIL = (AppiumBy.ID, "et_email")
    PASSWORD = (AppiumBy.ACCESSIBILITY_ID, "password-input")
    LOG_IN = (AppiumBy.ACCESSIBILITY_ID, "login-button")
    SIGN_UP = (AppiumBy.ACCESSIBILITY_ID, "signup-button")
    TITLE = (AppiumBy.ID, "title")

    def __init__(self, driver):
        self.driver = driver

    def log_in(self, email, password):
        self.driver.find_element(*self.EMAIL).send_keys(email)
        self.driver.find_element(*self.PASSWORD).send_keys(password)
        self.driver.find_element(*self.LOG_IN).click()

    def title(self):
        return self.driver.find_element(*self.TITLE).text
