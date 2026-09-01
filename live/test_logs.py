"""Vector tailing a file, its own HTTP sink, pandora's NDJSON door."""

import pytest

from live.support import body_of, issue_titled
from pandora.issues import models as issue_models

pytestmark = pytest.mark.live


def test_a_shipped_log_line_became_an_issue():
    """Should open the door to services that will never carry an SDK."""
    issue = issue_titled("QueueOverflow")

    assert issue is not None


def test_the_service_field_became_a_tag():
    """Should keep the field the shipper set, which is how the stream filters."""
    issue = issue_titled("QueueOverflow")

    result = set(
        issue_models.TagStat.objects.filter(issue=issue, key="service").values_list(
            "value", flat=True
        )
    )
    expected = {"shipper"}

    assert result == expected


def test_the_host_field_became_a_tag():
    """Should record where it came from, because that is the first question."""
    issue = issue_titled("QueueOverflow")

    result = set(
        issue_models.TagStat.objects.filter(issue=issue, key="host").values_list(
            "value", flat=True
        )
    )
    expected = {"live-node-1"}

    assert result == expected


def test_a_traceback_in_a_log_line_became_frames(signed_in, base_url):
    """Should parse the stack a log line carried, not print it as a wall of text."""
    body = body_of(signed_in, base_url, issue_titled("UpstreamRefused"))

    assert "pipeline.py" in body


def test_the_parsed_frame_carries_its_line_number(signed_in, base_url):
    """Should be an ordinary frame from here on, with everything a frame has."""
    body = body_of(signed_in, base_url, issue_titled("UpstreamRefused"))

    assert "self.sink.write(batch)" in body
