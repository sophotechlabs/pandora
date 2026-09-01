import http

import pytest
from django.contrib.auth import models as auth_models

from pandora.core.models import IngestToken, TokenScope, TokenSource
from pandora.ingest.models import EnvelopeState
from pandora.people import audit
from pandora.people.models import Membership, Role, Team
from pandora.releases.models import Release
from tests.ingest import helpers

pytestmark = pytest.mark.django_db


@pytest.fixture
def scoped_operator(operator, project):
    def build(*projects):
        team = Team.objects.create(name="platform")
        for one in projects:
            team.projects.add(one)
        Membership.objects.create(user=operator, team=team, role=Role.MEMBER)
        return operator

    return build


def titles(response):
    return [row.issue.title for row in response.context["rows"]]


def test_a_team_sees_only_its_own_projects_in_the_stream(
    operator_client, scoped_operator, make_issue, other_project, project
):
    """Should keep a shared install from showing every team everything."""
    scoped_operator(project)
    make_issue(title="ours is broken")
    make_issue(title="theirs is broken", project=other_project)

    result = titles(operator_client.get("/"))
    expected = ["ours is broken"]

    assert result == expected


def test_an_unscoped_operator_sees_every_project(
    operator_client, make_issue, other_project
):
    """Should leave a single-operator install exactly as it was."""
    make_issue(title="ours is broken")
    make_issue(title="theirs is broken", project=other_project)

    result = sorted(titles(operator_client.get("/")))
    expected = ["ours is broken", "theirs is broken"]

    assert result == expected


def test_an_issue_outside_the_scope_is_not_found(
    operator_client, scoped_operator, make_issue, other_project, project
):
    """Should answer the same for out-of-scope as for deleted — no existence leak."""
    scoped_operator(project)
    hidden = make_issue(project=other_project)

    result = operator_client.get(f"/issues/{hidden.pk}/").status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


def test_an_issue_inside_the_scope_opens(
    operator_client, scoped_operator, make_issue, project
):
    """Should not break the ordinary case while adding the scope."""
    scoped_operator(project)
    issue = make_issue()

    result = operator_client.get(f"/issues/{issue.pk}/").status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_the_overview_counts_only_the_scoped_projects(
    operator_client, scoped_operator, make_issue, other_project, project
):
    """Should not tell a team about a backlog it cannot see."""
    scoped_operator(project)
    make_issue()
    make_issue(project=other_project)
    make_issue(project=other_project)

    response = operator_client.get("/overview/")
    firing = next(kpi for kpi in response.context["kpis"] if kpi.label == "Firing now")

    result = firing.value
    expected = 1

    assert result == expected


def test_a_superuser_is_never_scoped(client, make_issue, other_project, project):
    """Should never hide anything from the person who runs the install."""
    root = auth_models.User.objects.create_superuser(
        username="root", password="root-pass"
    )
    team = Team.objects.create(name="platform")
    team.projects.add(project)
    Membership.objects.create(user=root, team=team, role=Role.MEMBER)
    client.force_login(root)
    make_issue(title="ours is broken")
    make_issue(title="theirs is broken", project=other_project)

    result = len(titles(client.get("/")))
    expected = 2

    assert result == expected


def test_segment_counts_do_not_include_other_projects(
    operator_client, scoped_operator, make_issue, other_project, project
):
    scoped_operator(project)
    make_issue()
    make_issue(project=other_project)

    segments = operator_client.get("/").context["segments"]
    counts = {segment.key: segment.count for segment in segments}

    assert counts["everything"] == 1
    assert counts["unresolved"] == 1


def test_bulk_actions_cannot_change_another_projects_issue(
    operator_client, scoped_operator, make_issue, other_project, project
):
    scoped_operator(project)
    hidden = make_issue(project=other_project)

    operator_client.post(
        "/issues/actions/",
        {"issue": [hidden.pk], "action": "resolve", "next": "/"},
    )

    hidden.refresh_from_db()
    assert hidden.triage_state == "new"


def test_occurrence_deletion_cannot_reach_another_projects_issue(
    operator_client, scoped_operator, make_issue, other_project, project
):
    scoped_operator(project)
    hidden = make_issue(project=other_project)

    response = operator_client.post(
        f"/issues/{hidden.pk}/occurrences/01ARZ3NDEKTSV4RRFFQ69G5FAV/delete/"
    )

    assert response.status_code == http.HTTPStatus.NOT_FOUND


def test_release_choices_do_not_name_another_projects_release(
    operator_client, scoped_operator, other_project, project
):
    scoped_operator(project)
    Release.objects.create(project=project, version="ours", sort_key="2")
    Release.objects.create(project=other_project, version="theirs", sort_key="3")

    choices = dict(operator_client.get("/").context["releases"])

    assert "ours" in choices
    assert "theirs" not in choices


def test_ingest_status_does_not_include_another_project(
    operator_client,
    scoped_operator,
    token,
    am_fixture,
    other_project,
    *,
    project,
):
    scoped_operator(project)
    other_token = IngestToken.objects.create(
        project=other_project,
        name="hidden token",
        token="hidden-token",
        source=TokenSource.AM,
        scope=TokenScope.INGEST,
    )
    own = helpers.store_envelope(am_fixture("firing_group"), token)
    hidden = helpers.store_envelope(am_fixture("firing_group"), other_token)
    own.state = EnvelopeState.FAILED
    own.save(update_fields=["state"])
    hidden.state = EnvelopeState.FAILED
    hidden.save(update_fields=["state"])

    response = operator_client.get("/ingest/")
    names = [entry.name for entry in response.context["tokens"]]

    assert response.context["backlog"] == 1
    assert names == [token.name]


def test_replay_does_not_process_another_projects_envelope(
    operator_client,
    scoped_operator,
    token,
    am_fixture,
    other_project,
    *,
    project,
):
    scoped_operator(project)
    other_token = IngestToken.objects.create(
        project=other_project,
        name="hidden token",
        token="hidden-token",
        source=TokenSource.AM,
        scope=TokenScope.INGEST,
    )
    own = helpers.store_envelope(am_fixture("firing_group"), token)
    hidden = helpers.store_envelope(am_fixture("firing_group"), other_token)
    own.state = EnvelopeState.FAILED
    own.save(update_fields=["state"])
    hidden.state = EnvelopeState.FAILED
    hidden.save(update_fields=["state"])

    operator_client.post("/ingest/replay/")

    own.refresh_from_db()
    hidden.refresh_from_db()
    assert own.state == EnvelopeState.DONE
    assert hidden.state == EnvelopeState.FAILED


def test_the_admin_issue_list_is_project_scoped(
    operator_client, scoped_operator, make_issue, other_project, project
):
    scoped_operator(project)
    own = make_issue(title="ours")
    make_issue(title="theirs", project=other_project)

    response = operator_client.get("/admin/issues/issue/", {"triage": "all"})
    result = [issue.pk for issue in response.context["cl"].result_list]

    assert result == [own.pk]


def test_the_admin_dashboard_is_project_scoped(
    operator_client, scoped_operator, make_issue, other_project, project
):
    scoped_operator(project)
    make_issue(title="ours", event_count=2)
    make_issue(title="theirs", project=other_project, event_count=100)

    dashboard = operator_client.get("/admin/").context["dashboard"]
    result = [row[0].text for row in dashboard.tables["issues"].rows]

    assert result == ["ours"]


def test_the_history_is_project_scoped(
    operator_client, scoped_operator, other_project, project
):
    scoped_operator(project)
    audit.record("operator", audit.TRIAGE, "ours", project_ids=[project.pk])
    audit.record("operator", audit.SNOOZE, "theirs", project_ids=[other_project.pk])
    audit.record("pandora", audit.CONFIG, "global")

    response = operator_client.get("/history/")
    entries = [entry for entry, _ in response.context["rows"]]

    assert [entry.target for entry in entries] == ["ours"]
    assert response.context["actions"] == [audit.TRIAGE]
