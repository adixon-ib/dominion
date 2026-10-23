"""Unit tests for install_temp_license with a scripted fake console."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from dominion.actions.license_temp import _parse_menu, install_temp_license

MENU = """\
 set temp_license

  1. DNSone (DNS, DHCP)
  2. DNSone with Grid (DNS, DHCP, Grid)
 16. Add Cloud Network Discovery license
 17. Add Cloud Platform license
 18. Add Security Ecosystem license

 **Temporary License Warning**
This software is currently operating under a temporary license.
1. The temporary license will expire in 60 days.
2. It is not recommended
3. It is recommended

Select license (1-21) or q to  quit:"""


CLOUD = "Add Cloud Platform license"
YN = "Are you sure (y or n):"


class FakeConsole:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.sent = []
        self.patterns = []

    def sendline(self, text):
        self.sent.append(text)

    def expect(self, pattern, timeout=None):
        self.patterns.append(pattern)
        return self.outputs.pop(0)


@pytest.fixture
def make_app():
    def make(console):
        app = MagicMock()

        @contextmanager
        def session(**kwargs):
            yield console

        app.console_connect_session = session
        return app
    return make


def test_parse_menu_ignores_warning_lines():
    menu = _parse_menu(MENU.replace("\n", "\r\n"))
    assert menu["1"] == "DNSone (DNS, DHCP)"
    assert menu["17"] == "Add Cloud Platform license"
    assert set(menu) == {"1", "2", "16", "17", "18"}


def test_selects_single_match_and_answers_prompts(make_app):
    c = FakeConsole([
        MENU,
        YN,
        "Temporary license is installed. Restart UI now (y or n):",
        "UI restarted",
    ])
    assert install_temp_license(make_app(c), "cloud platform") == CLOUD
    assert c.sent == ["set temp_license", "17", "y", "y"]


def test_prompt_return_after_confirmation_is_success(make_app):
    c = FakeConsole([MENU, YN, "Temporary license is installed\nInfoblox >"])
    install_temp_license(make_app(c), "Security")
    assert c.sent == ["set temp_license", "18", "y"]


@pytest.mark.parametrize("match", ["nonexistent", "DNSone"])
def test_no_or_ambiguous_match_sends_q_and_lists_menu(make_app, match):
    c = FakeConsole([MENU, "Infoblox >"])
    with pytest.raises(RuntimeError, match=r"(?s)need exactly 1.*17\. " + CLOUD):
        install_temp_license(make_app(c), match)
    assert c.sent == ["set temp_license", "q"]


def test_already_exists_returns_without_error(make_app):
    c = FakeConsole([MENU, YN, "License already exists", "Infoblox >"])
    assert install_temp_license(make_app(c), "Cloud Platform") == CLOUD
    assert c.sent == ["set temp_license", "17", "y"]


def test_no_confirmation_raises(make_app):
    c = FakeConsole([MENU, "Infoblox >"])
    with pytest.raises(RuntimeError, match="No install confirmation"):
        install_temp_license(make_app(c), "Cloud Platform")
