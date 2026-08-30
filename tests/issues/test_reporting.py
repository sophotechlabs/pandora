import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.issues import models as issue_models
from pandora.issues import reporting

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def resolve(project):
    def build(hours, source=core_models.TokenSource.SDK, digest=None):
        issue = issue_models.Issue.objects.create(
            project=project,
            fingerprint_hash=digest or f"{hours:064d}",
            title=f"resolved after {hours}h",
            first_seen=NOW - datetime.timedelta(hours=hours),
            last_seen=NOW,
        )
        if source == core_models.TokenSource.AM:
            issue_models.Episode.objects.create(
                project=project,
                issue=issue,
                am_fingerprint=f"am{hours}",
                labels={},
                starts_at=NOW - datetime.timedelta(hours=hours),
            )
        issue_models.IssueActivity.objects.create(
            issue=issue,
            kind=issue_models.ActivityKind.RESOLVED,
            actor="dev",
            at=NOW,
        )
        return issue

    return build


# the numbers


def test_an_install_with_nothing_resolved_reports_zero(project):
    """Should publish a number rather than nothing at all."""
    result = reporting.refresh(NOW)
    expected = {core_models.TokenSource.AM: 0.0, core_models.TokenSource.SDK: 0.0}

    assert result == expected


def test_one_resolution_is_its_own_median(resolve):
    """Should measure first-seen to resolved, which is what people mean by MTTR."""
    resolve(4)

    result = reporting.refresh(NOW)[core_models.TokenSource.SDK]
    expected = 4 * 3600

    assert result == expected


def test_the_median_of_three_is_the_middle_one(resolve):
    """Should be a median, not a mean — one week-long issue must not dominate."""
    resolve(1)
    resolve(3)
    resolve(100)

    result = reporting.refresh(NOW)[core_models.TokenSource.SDK]
    expected = 3 * 3600

    assert result == expected


def test_the_median_of_an_even_count_is_the_midpoint(resolve):
    """Should not pick arbitrarily between the two middle values."""
    resolve(2)
    resolve(4)

    result = reporting.refresh(NOW)[core_models.TokenSource.SDK]
    expected = 3 * 3600

    assert result == expected


def test_alertmanager_and_sdk_are_measured_apart(resolve):
    """Should be the caveat Rollbar prints and nobody acts on.

    An Alertmanager issue resolves itself when the alert clears, so mixing the
    two produces a number that describes the monitoring, not the team.
    """
    resolve(1, source=core_models.TokenSource.AM)
    resolve(10, source=core_models.TokenSource.SDK)

    result = reporting.refresh(NOW)
    expected = {
        core_models.TokenSource.AM: 3600.0,
        core_models.TokenSource.SDK: 10 * 3600.0,
    }

    assert result == expected


def test_a_resolution_outside_the_window_is_ignored(resolve, project):
    """Should describe the last thirty days, not the whole history."""
    issue = resolve(4)
    issue_models.IssueActivity.objects.filter(issue=issue).update(
        at=NOW - datetime.timedelta(days=60)
    )

    result = reporting.refresh(NOW)[core_models.TokenSource.SDK]
    expected = 0.0

    assert result == expected


def test_the_latest_resolution_of_a_reopened_issue_counts(resolve, project):
    """Should measure to the resolve that stuck, not the first attempt."""
    issue = resolve(4)
    issue_models.IssueActivity.objects.create(
        issue=issue,
        kind=issue_models.ActivityKind.RESOLVED,
        actor="dev",
        at=NOW + datetime.timedelta(hours=2),
    )

    result = reporting.refresh(NOW)[core_models.TokenSource.SDK]
    expected = 6 * 3600

    assert result == expected


def test_the_resolved_count_is_published_too(resolve):
    """Should let a reader see how many issues the median is built from."""
    resolve(1)
    resolve(2)

    result = len(reporting.resolutions(NOW))
    expected = 2

    assert result == expected


# the gauges


def test_the_gauge_carries_the_median(resolve):
    """Should be readable from /metrics without a chart product."""
    from prometheus_client import REGISTRY

    resolve(4)
    reporting.refresh(NOW)

    result = REGISTRY.get_sample_value(
        "pandora_mttr_seconds", {"source": core_models.TokenSource.SDK}
    )
    expected = 4 * 3600

    assert result == expected


def test_the_gauge_carries_the_count(resolve):
    """Should publish the sample size beside the number built from it."""
    from prometheus_client import REGISTRY

    resolve(4)
    reporting.refresh(NOW)

    result = REGISTRY.get_sample_value(
        "pandora_resolved_issues", {"source": core_models.TokenSource.SDK}
    )
    expected = 1

    assert result == expected


def test_a_resolution_for_an_issue_that_has_gone_is_skipped(resolve, project):
    """Should not divide by an issue prune already took."""
    issue = resolve(4)
    issue_models.Issue.objects.filter(pk=issue.pk).delete()

    result = reporting.resolutions(NOW)

    assert result == []
