import http

import pytest
from django.contrib.auth import models as auth_models

from pandora.issues import models as issue_models
from pandora.people.models import Membership, Role, Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def member_of(db, client, project):
    def build(role):
        user = auth_models.User.objects.create_user(
            username=f"{role}-user", password="pass", is_staff=True
        )
        team = Team.objects.create(name=f"{role}-team")
        team.projects.add(project)
        Membership.objects.create(user=user, team=team, role=role)
        client.force_login(user)
        return client

    return build


def triage(session, issue):
    return session.post(
        "/issues/actions/",
        {"issue": [issue.pk], "action": "acknowledge", "next": "/"},
    )


# a viewer


def test_a_viewer_reaches_the_stream(member_of):
    """Should let someone watch the queue — that is the whole point of the role."""
    result = member_of(Role.VIEWER).get("/").status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_a_viewer_may_not_triage(member_of, make_issue):
    """Should keep the queue's state owned by the people doing the work."""
    issue = make_issue()

    result = triage(member_of(Role.VIEWER), issue).status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


def test_a_refused_triage_changes_nothing(member_of, make_issue):
    """Should not half-apply the action it just refused."""
    issue = make_issue()

    triage(member_of(Role.VIEWER), issue)

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_a_viewer_is_not_offered_the_action_bar(member_of, make_issue):
    """Should not show a button that answers 403 — that reads as a bug."""
    make_issue()

    body = member_of(Role.VIEWER).get("/").content.decode()

    assert "issues/actions/" not in body


def test_a_viewer_may_not_replay(member_of):
    """Should keep re-running the ingest queue away from a read-only account."""
    result = member_of(Role.VIEWER).post("/ingest/replay/").status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


def test_a_viewer_may_not_delete_an_occurrence(member_of, make_issue):
    """Should keep the one destructive button in the UI out of read-only hands."""
    issue = make_issue()

    response = member_of(Role.VIEWER).post(
        f"/issues/{issue.pk}/occurrences/01H0/delete/"
    )

    result = response.status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


# a member


def test_a_member_may_triage(member_of, make_issue):
    """Should let the people on call clear the queue."""
    issue = make_issue()

    triage(member_of(Role.MEMBER), issue)

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.ACKNOWLEDGED

    assert result == expected


def test_a_member_is_offered_the_action_bar(member_of, make_issue):
    """Should show the buttons to the role that may press them."""
    make_issue()

    body = member_of(Role.MEMBER).get("/").content.decode()

    assert "issues/actions/" in body


def test_a_member_may_not_replay(member_of):
    """Should keep re-processing every stored envelope an owner's decision."""
    result = member_of(Role.MEMBER).post("/ingest/replay/").status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


# an owner


def test_an_owner_may_replay(member_of):
    """Should let whoever runs the install unblock the ingest queue."""
    result = member_of(Role.OWNER).post("/ingest/replay/").status_code
    expected = http.HTTPStatus.FOUND

    assert result == expected


def test_an_owner_may_triage(member_of, make_issue):
    """Should not have to grant an owner the lesser role as well."""
    issue = make_issue()

    triage(member_of(Role.OWNER), issue)

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.ACKNOWLEDGED

    assert result == expected
