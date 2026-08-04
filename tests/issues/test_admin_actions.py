import hashlib
import http

import freezegun
import pytest
from django.contrib import messages as django_messages
from django.utils import timezone

from pandora.issues import models

pytestmark = pytest.mark.django_db

CHANGELIST = "/admin/issues/issue/"
FROZEN = "2026-08-04 12:00:00"


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time(FROZEN):
        yield


def make_issue(project, title, **overrides):
    fields = {
        "fingerprint_hash": hashlib.sha256(title.encode()).hexdigest(),
        "fingerprint": [f"alertname:{title}"],
        "grouping_labels": {"alertname": title},
        "title": title,
        "culprit": f"alertname={title}",
        "level": models.Level.ERROR,
        "environment": "p-mk1",
        "triage_state": models.TriageState.NEW,
        "source_state": models.SourceState.FIRING,
        "first_seen": timezone.now(),
        "last_seen": timezone.now(),
    }
    fields.update(overrides)
    return models.Issue.objects.create(project=project, **fields)


def run(client, action, issues, **params):
    payload = {
        "action": action,
        "_selected_action": [str(issue.pk) for issue in issues],
        "index": "0",
    }
    query = ""
    if params:
        query = "?" + "&".join(f"{key}={value}" for key, value in params.items())
    return client.post(f"{CHANGELIST}{query}", payload)


def notes(response):
    return [
        str(message) for message in django_messages.get_messages(response.wsgi_request)
    ]


# action wiring


def test_an_action_post_redirects_back_to_the_changelist(admin_client, project):
    """Should behave like every other admin bulk action."""
    issue = make_issue(project, "One")

    response = run(admin_client, "acknowledge", [issue])

    result = response.status_code
    expected = http.HTTPStatus.FOUND

    assert result == expected


# acknowledge


def test_acknowledge_moves_a_new_issue(admin_client, project):
    """Should mark that a human has picked the issue up."""
    issue = make_issue(project, "One")

    run(admin_client, "acknowledge", [issue])

    result = models.Issue.objects.get(pk=issue.pk).triage_state
    expected = models.TriageState.ACKNOWLEDGED

    assert result == expected


def test_acknowledge_leaves_the_resolution_stamp_alone(admin_client, project):
    """Should not pretend an acknowledged issue was closed."""
    issue = make_issue(project, "One")

    run(admin_client, "acknowledge", [issue])

    result = models.Issue.objects.get(pk=issue.pk).last_resolved_at
    expected = None

    assert result == expected


# resolve


def test_resolve_stamps_when_the_issue_was_closed(admin_client, project):
    """Should date the close so a later episode reads as a regression."""
    issue = make_issue(project, "One")

    run(admin_client, "resolve", [issue])

    stored = models.Issue.objects.get(pk=issue.pk)

    result = (stored.triage_state, stored.last_resolved_at)
    expected = (models.TriageState.RESOLVED, timezone.now())

    assert result == expected


# ignore


def test_ignore_moves_a_new_issue(admin_client, project):
    """Should let an operator mute a known-noisy issue."""
    issue = make_issue(project, "One")

    run(admin_client, "ignore", [issue])

    result = models.Issue.objects.get(pk=issue.pk).triage_state
    expected = models.TriageState.IGNORED

    assert result == expected


# audit trail


def test_an_action_writes_one_activity_row(admin_client, project):
    """Should leave a trace of who changed the state and from what."""
    issue = make_issue(project, "One")

    run(admin_client, "acknowledge", [issue])

    activity = models.IssueActivity.objects.get(issue=issue)

    result = (activity.kind, activity.actor, activity.data)
    expected = ("acknowledged", "admin", {"previous_triage_state": "new"})

    assert result == expected


def test_reopening_a_resolved_issue_is_logged_as_a_reopen(admin_client, project):
    """Should distinguish a hand-reopen from a fresh acknowledge in the feed."""
    issue = make_issue(project, "One", triage_state=models.TriageState.RESOLVED)

    run(admin_client, "acknowledge", [issue], triage="all")

    result = models.IssueActivity.objects.get(issue=issue).kind
    expected = "reopened"

    assert result == expected


def test_a_repeated_action_writes_nothing(admin_client, project):
    """Should keep a second click from filling the audit trail with noise."""
    issue = make_issue(project, "One", triage_state=models.TriageState.ACKNOWLEDGED)

    run(admin_client, "acknowledge", [issue])

    result = models.IssueActivity.objects.filter(issue=issue).count()
    expected = 0

    assert result == expected


# bulk reporting


def test_a_bulk_action_reports_what_it_changed(admin_client, project):
    """Should say how many rows moved and how many were already there."""
    moved = make_issue(project, "Moved")
    already = make_issue(
        project, "Already", triage_state=models.TriageState.ACKNOWLEDGED
    )

    response = run(admin_client, "acknowledge", [moved, already])

    result = notes(response)
    expected = ["Acknowledged 1 issue(s), 1 unchanged"]

    assert result == expected


def test_a_bulk_action_moves_every_selected_issue(admin_client, project):
    """Should apply to the whole selection, not the first row."""
    issues = [make_issue(project, f"Issue {index}") for index in range(3)]

    run(admin_client, "resolve", issues)

    result = models.Issue.objects.filter(
        triage_state=models.TriageState.RESOLVED
    ).count()
    expected = 3

    assert result == expected


def test_an_action_leaves_unselected_issues_alone(admin_client, project):
    """Should never widen a bulk action past the checked boxes."""
    selected = make_issue(project, "Selected")
    untouched = make_issue(project, "Untouched")

    run(admin_client, "resolve", [selected])

    result = models.Issue.objects.get(pk=untouched.pk).triage_state
    expected = models.TriageState.NEW

    assert result == expected


# end to end


def test_a_resolved_issue_leaves_the_default_view(admin_client, project):
    """Should take the issue off the triage list once it is closed."""
    issue = make_issue(project, "One")

    run(admin_client, "resolve", [issue])
    listing = admin_client.get(CHANGELIST)

    result = listing.context["cl"].result_count
    expected = 0

    assert result == expected
    assert models.Issue.objects.count() == 1
