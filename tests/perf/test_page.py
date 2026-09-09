import datetime

import pytest

from pandora.perf import service

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 9, 9, 12, 30, tzinfo=datetime.UTC)


@pytest.fixture
def endpoint(project):
    def build(name="GET /checkout", duration_ms=120, count=1, moment=None):
        started = (moment or NOW).timestamp()
        for _ in range(count):
            service.record(
                project,
                {
                    "type": "transaction",
                    "transaction": name,
                    "start_timestamp": started,
                    "timestamp": started + duration_ms / 1000,
                    "contexts": {"trace": {"status": "ok"}},
                },
                NOW,
            )

    return build


def test_the_page_lists_the_endpoints(operator_client, endpoint):
    """Should be the surface the aggregation exists for."""
    endpoint()

    response = operator_client.get("/performance/")

    assert response.status_code == 200
    assert "GET /checkout" in response.content.decode()


def test_the_page_is_empty_before_any_transaction(operator_client):
    """Should say so rather than render an empty table with no explanation."""
    response = operator_client.get("/performance/")

    assert "No transaction has been received yet" in response.content.decode()


def test_the_slowest_endpoint_is_listed_first(operator_client, endpoint):
    """Should put the endpoint a reader came to find at the top."""
    endpoint(name="GET /fast", duration_ms=5, count=5)
    endpoint(name="GET /slow", duration_ms=3000, count=5)

    response = operator_client.get("/performance/")

    rows = [reading.transaction for _, reading in response.context["rows"]]
    assert rows[0] == "GET /slow"


def test_the_window_can_be_widened(operator_client, endpoint):
    """Should let a reader look past the last day."""
    endpoint(moment=NOW - datetime.timedelta(days=3))

    response = operator_client.get("/performance/", {"hours": "168"})

    assert len(response.context["rows"]) == 1


def test_an_unusable_window_falls_back_to_the_default(operator_client):
    """Should not let a hand-edited URL reach the query as a window."""
    response = operator_client.get("/performance/", {"hours": "drop table"})

    assert response.context["window_hours"] == 24


def test_picking_an_endpoint_shows_where_its_time_went(operator_client, project):
    """Should answer 'database or network' from the summed spans."""
    started = NOW.timestamp()
    service.record(
        project,
        {
            "type": "transaction",
            "transaction": "GET /checkout",
            "start_timestamp": started,
            "timestamp": started + 0.2,
            "spans": [
                {"op": "db", "start_timestamp": started, "timestamp": started + 0.15}
            ],
        },
        NOW,
    )

    response = operator_client.get("/performance/", {"transaction": "GET /checkout"})

    assert response.context["breakdown"] == [("db", 1, 150.0)]


def test_a_reader_only_sees_their_own_projects(client, make_user, project, endpoint):
    """Should honour the project scope every other page honours."""
    endpoint()
    from pandora.core import models as core_models
    from pandora.people import models as people_models

    elsewhere = core_models.Project.objects.create(slug="elsewhere", name="Elsewhere")
    user = make_user("scoped")
    team = people_models.Team.objects.create(name="other")
    team.projects.add(elsewhere)
    people_models.Membership.objects.create(
        team=team, user=user, role=people_models.Role.MEMBER
    )
    client.force_login(user)

    response = client.get("/performance/")

    assert response.context["rows"] == []
