import datetime

import pytest
from django.utils import timezone

from pandora.issues import models as issue_models
from pandora.releases import service
from tests.ingest.test_sdk_processor import deliver, event_payload

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def released(project, version, event_id="1" * 32):
    payload = event_payload(event_id=event_id, release=version)
    deliver(project, payload)
    return issue_models.Issue.objects.get()


def resolve(issue, project, version=None, in_next=False):
    from pandora.releases import models as release_models

    release = None
    if version is not None:
        release = release_models.Release.objects.get(project=project, version=version)
    issue_models.Issue.objects.filter(pk=issue.pk).update(
        triage_state=issue_models.TriageState.RESOLVED,
        last_resolved_at=NOW - datetime.timedelta(hours=1),
    )
    service.resolve_in(issue, release=release, in_next=in_next, at=NOW)


def state(issue):
    return issue_models.Issue.objects.get(pk=issue.pk).triage_state


# resolved in a specific release


def test_an_event_on_a_later_release_reopens_the_issue(project):
    """Should be the ordinary regression, now measured against a version."""
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3")

    released(project, "1.2.4", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_an_event_on_the_same_release_leaves_it_resolved(project):
    """Should be Countly's reoccurred semantics, which nobody free implements.

    A pod still running the old image is not the fix failing.
    """
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3")

    released(project, "1.2.3", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_an_event_on_an_earlier_release_leaves_it_resolved(project):
    """Should not reopen a fixed issue because one replica lagged behind."""
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3")

    released(project, "1.2.2", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


# resolved in the next release


def test_the_next_release_reopens_it(project):
    """Should be the promise: fixed in whatever ships after this."""
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3", in_next=True)

    released(project, "1.3.0", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_the_release_it_was_resolved_at_does_not_reopen_it(project):
    """Should hold the promise until something newer than it arrives."""
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3", in_next=True)

    released(project, "1.2.3", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


# the plain case


def test_an_issue_resolved_without_a_release_still_regresses(project):
    """Should not change what an install with no releases already does."""
    issue = released(project, "1.2.3")
    issue_models.Issue.objects.filter(pk=issue.pk).update(
        triage_state=issue_models.TriageState.RESOLVED,
        last_resolved_at=NOW - datetime.timedelta(hours=1),
    )

    released(project, "1.2.3", event_id="2" * 32)

    result = state(issue)
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_an_event_with_no_release_reopens_a_release_resolved_issue(project):
    """Should reopen rather than assume, when the event cannot say what it runs."""
    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3")

    deliver(project, event_payload(event_id="2" * 32))

    result = state(issue)
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_a_resolution_reads_as_what_it_promised(project):
    """Should be legible in the admin without following two ids."""
    from pandora.releases import models as release_models

    issue = released(project, "1.2.3")
    resolve(issue, project, "1.2.3", in_next=True)

    result = str(release_models.Resolution.objects.get())

    assert "next release" in result
