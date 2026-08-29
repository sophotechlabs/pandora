import http

import pytest
from django.contrib.auth import models as auth_models

from pandora.people import audit
from pandora.people.models import AuditEntry

pytestmark = pytest.mark.django_db


def actions(response):
    return [entry.action for entry, _ in response.context["rows"]]


def only(action):
    return AuditEntry.objects.get(action=action)


# what the page shows


def test_a_filter_that_matches_nothing_says_so(operator_client):
    """Should say so rather than showing an empty table with no explanation."""
    body = operator_client.get("/history/?actor=nobody").content.decode()

    assert "Nothing recorded yet" in body


def test_the_newest_action_is_at_the_top(operator_client):
    """Should read like a log."""
    audit.record("dev", audit.SIGN_IN)
    audit.record("dev", audit.TRIAGE, "17")

    result = actions(operator_client.get("/history/?actor=dev"))
    expected = ["issue.triage", "auth.sign-in"]

    assert result == expected


def test_the_detail_of_an_entry_is_rendered(operator_client):
    """Should show what changed, not only that something did."""
    audit.record("dev", audit.TRIAGE, "17", {"state": "resolved"})

    body = operator_client.get("/history/").content.decode()

    assert "state=resolved" in body


def test_an_action_with_no_actor_is_shown_as_pandora(operator_client):
    """Should distinguish a command run on the box from a person clicking."""
    audit.record("", audit.CONFIG, "pandora.yml")

    body = operator_client.get("/history/").content.decode()

    assert "pandora</td>" in body


def test_the_log_can_be_filtered_to_one_action(operator_client):
    """Should answer "who resolved things this week" without reading everything."""
    audit.record("dev", audit.SIGN_IN)
    audit.record("dev", audit.TRIAGE, "17")

    result = actions(operator_client.get("/history/?action=issue.triage"))
    expected = ["issue.triage"]

    assert result == expected


def test_the_log_can_be_filtered_to_one_person(operator_client):
    """Should answer "what did this account do" — the question after an incident."""
    audit.record("dev", audit.TRIAGE, "17")
    audit.record("ops", audit.TRIAGE, "18")

    response = operator_client.get("/history/?actor=ops")

    result = [entry.actor for entry, _ in response.context["rows"]]
    expected = ["ops"]

    assert result == expected


def test_only_the_actions_that_happened_are_offered_as_filters(operator_client):
    """Should not offer a filter that can only ever return nothing."""
    audit.record("dev", audit.TRIAGE, "17")

    result = operator_client.get("/history/").context["actions"]
    expected = ["auth.sign-in", "issue.triage"]

    assert result == expected


def test_a_stranger_is_sent_to_the_login_page(client):
    """Should not leak who works here to someone who is not signed in."""
    response = client.get("/history/")

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/login/?next=/history/")

    assert result == expected


def test_the_page_is_linked_from_the_navigation(operator_client):
    """Should be findable without knowing the URL."""
    body = operator_client.get("/").content.decode()

    assert 'href="/history/"' in body


# what gets recorded


def test_triage_is_recorded(operator_client, make_issue, operator):
    """Should record the action people take most, or the log proves nothing."""
    issue = make_issue()

    operator_client.post(
        "/issues/actions/", {"issue": [issue.pk], "action": "resolve", "next": "/"}
    )

    entry = only("issue.triage")
    result = (entry.actor, entry.action, entry.target, entry.data)
    expected = (
        "operator",
        "issue.triage",
        str(issue.pk),
        {"state": "resolved", "changed": 1},
    )

    assert result == expected


def test_a_bulk_triage_is_recorded_once_with_the_count(operator_client, make_issue):
    """Should not write forty rows when someone selects forty issues."""
    first = make_issue()
    second = make_issue()

    operator_client.post(
        "/issues/actions/",
        {"issue": [first.pk, second.pk], "action": "resolve", "next": "/"},
    )

    entry = only("issue.triage")
    result = (entry.target, entry.data["changed"])
    expected = (f"{first.pk},{second.pk}", 2)

    assert result == expected


def test_a_snooze_is_recorded_with_its_terms(operator_client, make_issue):
    """Should show why an issue stopped notifying, months later."""
    issue = make_issue()

    operator_client.post(
        "/issues/actions/", {"issue": [issue.pk], "action": "snooze:4h", "next": "/"}
    )

    entry = only("issue.snooze")
    result = (entry.action, entry.data["spec"])
    expected = ("issue.snooze", "4h")

    assert result == expected


def test_a_replay_is_recorded_with_what_it_did(operator_client):
    """Should record the one action that can re-create data."""
    operator_client.post("/ingest/replay/")

    entry = only("ingest.replay")
    result = (entry.action, sorted(entry.data))
    expected = ("ingest.replay", ["attempted", "done", "failed"])

    assert result == expected


def test_a_refused_action_records_nothing(client, make_issue):
    """Should not fill the log with attempts that changed nothing."""
    reader = auth_models.User.objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    client.force_login(reader)
    issue = make_issue()

    client.post(
        "/issues/actions/", {"issue": [issue.pk], "action": "resolve", "next": "/"}
    )

    assert AuditEntry.objects.filter(action=audit.TRIAGE).count() == 0
