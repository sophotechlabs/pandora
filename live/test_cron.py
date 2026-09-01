"""The Go wrapper, built static and run in a bare alpine, around a failing job."""

import pytest

from live.support import body_of, issue_titled
from pandora.ingest import models as ingest_models

pytestmark = pytest.mark.live


def test_the_first_check_in_created_the_monitor():
    """Should need no configuration step — a job that reports is a job watched."""
    result = ingest_models.Monitor.objects.filter(slug="nightly-backup").count()
    expected = 1

    assert result == expected


def test_the_monitor_recorded_the_failure():
    """Should end in error, because the wrapped command exited 3."""
    monitor = ingest_models.Monitor.objects.get(slug="nightly-backup")

    result = monitor.status
    expected = ingest_models.MonitorStatus.ERROR

    assert result == expected


def test_the_failure_opened_an_issue():
    """Should be an ordinary issue, so it triages like everything else."""
    issue = issue_titled("CommandFailed")

    assert issue is not None


def test_the_issue_carries_the_exit_code(signed_in, base_url):
    """Should say 3, which is what tells two failures of one job apart."""
    body = body_of(signed_in, base_url, issue_titled("CommandFailed"))

    assert "exit_code" in body


def test_the_issue_carries_the_output(signed_in, base_url):
    """Should keep what the command printed, which is usually the answer."""
    body = body_of(signed_in, base_url, issue_titled("CommandFailed"))

    assert "disk full" in body
