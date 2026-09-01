"""Alerts and application errors in one store, which is the differentiation."""

import pytest

from live.support import body_of, issue_titled

pytestmark = pytest.mark.live


def test_the_error_page_shows_what_else_was_firing(signed_in, base_url):
    """Should answer *was anything else wrong at the time* without a second tool."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "LiveTargetDown" in body
