import http

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth import models as auth_models
from django.utils import timezone

from pandora.issues import models
from tests.am import fake_am

pytestmark = pytest.mark.django_db

ACTIONS = "/issues/actions/"


@pytest.fixture
def alertmanager(settings):
    server = fake_am.FakeAlertmanager().start()
    settings.PANDORA_AM_URL = server.url
    settings.PANDORA_AM_CA_BUNDLE = ""
    yield server
    server.stop()


def run(client, action, issues, next_url="/"):
    payload = {
        "action": action,
        "issue": [str(issue.pk) for issue in issues],
        "next": next_url,
    }
    return client.post(ACTIONS, payload)


def notes(response):
    return [
        str(message) for message in django_messages.get_messages(response.wsgi_request)
    ]


# triage


def test_acknowledge_moves_the_selected_issues(operator_client, make_issue):
    """Should mark that a human has picked the issue up."""
    issue = make_issue()

    run(operator_client, "acknowledge", [issue])

    result = models.Issue.objects.get(pk=issue.pk).triage_state
    expected = models.TriageState.ACKNOWLEDGED

    assert result == expected


def test_resolve_stamps_when_the_issue_was_closed(operator_client, make_issue):
    """Should date the close so a later episode reads as a regression."""
    issue = make_issue()

    run(operator_client, "resolve", [issue])

    stored = models.Issue.objects.get(pk=issue.pk)

    result = (stored.triage_state, stored.last_resolved_at)
    expected = (models.TriageState.RESOLVED, timezone.now())

    assert result == expected


def test_ignore_mutes_a_known_noisy_issue(operator_client, make_issue):
    """Should keep a permanent alert out of the default view."""
    issue = make_issue()

    run(operator_client, "ignore", [issue])

    result = models.Issue.objects.get(pk=issue.pk).triage_state
    expected = models.TriageState.IGNORED

    assert result == expected


def test_a_bulk_action_moves_every_selected_issue(operator_client, make_issue):
    """Should apply to the whole selection, not the first row."""
    issues = [make_issue(title=f"Issue {index}") for index in range(3)]

    run(operator_client, "resolve", issues)

    result = models.Issue.objects.filter(
        triage_state=models.TriageState.RESOLVED
    ).count()
    expected = 3

    assert result == expected


def test_an_unselected_issue_is_left_alone(operator_client, make_issue):
    """Should never widen a bulk action past the checked boxes."""
    selected = make_issue(title="Selected")
    untouched = make_issue(title="Untouched")

    run(operator_client, "resolve", [selected])

    result = models.Issue.objects.get(pk=untouched.pk).triage_state
    expected = models.TriageState.NEW

    assert result == expected


def test_an_action_reports_what_it_changed(operator_client, make_issue):
    """Should say how many rows moved and how many were already there."""
    moved = make_issue(title="Moved")
    already = make_issue(title="Already", triage_state=models.TriageState.ACKNOWLEDGED)

    response = run(operator_client, "acknowledge", [moved, already])

    result = notes(response)
    expected = ["Acknowledged 1 issue(s), 1 unchanged"]

    assert result == expected


def test_an_action_writes_one_activity_row(operator_client, make_issue):
    """Should leave the same audit trail the admin leaves."""
    issue = make_issue()

    run(operator_client, "acknowledge", [issue])

    activity = models.IssueActivity.objects.get(issue=issue)

    result = (activity.kind, activity.actor, activity.data)
    expected = ("acknowledged", "operator", {"previous_triage_state": "new"})

    assert result == expected


def test_reopening_a_resolved_issue_is_logged_as_a_reopen(operator_client, make_issue):
    """Should distinguish a hand-reopen from a fresh acknowledge in the feed."""
    issue = make_issue(triage_state=models.TriageState.RESOLVED)

    run(operator_client, "acknowledge", [issue])

    result = models.IssueActivity.objects.get(issue=issue).kind
    expected = "reopened"

    assert result == expected


# silences


def test_a_silence_reaches_alertmanager(operator_client, make_issue, alertmanager):
    """Should create the silence rather than only record the intent."""
    issue = make_issue(grouping_labels={"alertname": "TargetDown"})

    run(operator_client, "silence:1h", [issue])

    result = len(alertmanager.silences)
    expected = 1

    assert result == expected


def test_a_silence_is_recorded_against_the_issue(
    operator_client, make_issue, alertmanager
):
    """Should let the operator lift it later from the record."""
    issue = make_issue(grouping_labels={"alertname": "TargetDown"})

    run(operator_client, "silence:4h", [issue])

    result = models.SilenceLink.objects.filter(issue=issue).count()
    expected = 1

    assert result == expected


def test_a_silence_reports_the_window_it_used(
    operator_client, make_issue, alertmanager
):
    """Should confirm the duration back to whoever clicked."""
    issue = make_issue(grouping_labels={"alertname": "TargetDown"})

    response = run(operator_client, "silence:1d", [issue])

    result = notes(response)
    expected = ["Silenced 1 issue(s) in Alertmanager for 1d"]

    assert result == expected


def test_an_issue_with_no_grouping_labels_is_refused(
    operator_client, make_issue, alertmanager
):
    """Should not turn an unlabelled issue into a silence matching everything."""
    issue = make_issue(grouping_labels={})

    response = run(operator_client, "silence:1h", [issue])

    result = [note for note in notes(response) if "not silenced" in note]

    assert len(result) == 1
    assert not alertmanager.silences


def test_an_unknown_window_is_refused(operator_client, make_issue):
    """Should not silence for a duration nobody offered."""
    issue = make_issue()

    response = run(operator_client, "silence:1y", [issue])

    result = notes(response)
    expected = ["1y is not a silence window"]

    assert result == expected


def test_a_missing_alertmanager_url_is_reported(operator_client, make_issue, settings):
    """Should say why nothing was sent instead of failing silently."""
    settings.PANDORA_AM_URL = ""
    issue = make_issue()

    response = run(operator_client, "silence:1h", [issue])

    result = [note for note in notes(response) if "No silence sent" in note]

    assert len(result) == 1


# guard rails


def test_an_unknown_action_is_refused(operator_client, make_issue):
    """Should name the action rather than do something unexpected."""
    issue = make_issue()

    response = run(operator_client, "delete-everything", [issue])

    result = notes(response)
    expected = ["delete-everything is not an action"]

    assert result == expected
    assert models.Issue.objects.get(pk=issue.pk).triage_state == models.TriageState.NEW


def test_an_empty_selection_says_so(operator_client):
    """Should not report a successful action over nothing."""
    response = operator_client.post(ACTIONS, {"action": "resolve", "next": "/"})

    result = notes(response)
    expected = ["No issue was selected"]

    assert result == expected


def test_an_action_returns_to_the_view_it_came_from(operator_client, make_issue):
    """Should keep the reader's filter and page after a bulk action."""
    issue = make_issue()

    response = run(operator_client, "resolve", [issue], next_url="/?q=is%3Anew")

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/?q=is%3Anew")

    assert result == expected


def test_an_off_site_return_url_is_ignored(operator_client, make_issue):
    """Should not let a crafted form bounce the operator off the host."""
    issue = make_issue()

    response = run(operator_client, "resolve", [issue], next_url="https://evil.test/")

    result = response.url
    expected = "/"

    assert result == expected


def test_an_action_needs_a_post(operator_client):
    """Should keep a link or a prefetch from changing triage state."""
    response = operator_client.get(ACTIONS)

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_triage_needs_the_change_permission(client, make_issue):
    """Should read-protect the buttons the same way the admin protects the form."""
    watcher = auth_models.User.objects.create_user(
        username="watcher",
        password="watcher-pass",
        is_staff=True,
    )
    client.force_login(watcher)
    issue = make_issue()

    response = run(client, "resolve", [issue])

    assert response.status_code == http.HTTPStatus.FORBIDDEN
    assert models.Issue.objects.get(pk=issue.pk).triage_state == models.TriageState.NEW


def test_a_stranger_cannot_reach_the_action_endpoint(client, make_issue):
    """Should send an unauthenticated post to the login page, not to the handler."""
    issue = make_issue()

    response = run(client, "resolve", [issue])

    assert response.status_code == http.HTTPStatus.FOUND
    assert response.url.startswith("/login/")
    assert models.Issue.objects.get(pk=issue.pk).triage_state == models.TriageState.NEW
