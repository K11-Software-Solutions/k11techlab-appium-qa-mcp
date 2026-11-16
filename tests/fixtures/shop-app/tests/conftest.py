import os

import pytest
from fakedriver import FakeAppiumDriver


@pytest.fixture
def driver():
    d = FakeAppiumDriver(os.environ["K11_FAKE_SCREEN"])
    yield d
    d.quit()
