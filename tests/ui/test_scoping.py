import http

import pytest
from django.contrib.auth import models as auth_models

from pandora.people.models import Membership, Role, Team

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
