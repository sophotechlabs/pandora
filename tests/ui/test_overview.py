import datetime
import http

import pytest
from django.utils import timezone

from pandora.issues import models

pytestmark = pytest.mark.django_db


def body(client):
    return client.get("/overview/").content.decode()


def test_the_overview_names_the_headline_numbers(operator_client):
    """Should answer what is on fire, what is new, what came back, what is untouched."""
    page = body(operator_client)

    for label in (
        "Firing now",
        "New in 24 hours",
        "Regressions in 7 days",
        "Untriaged",
        "Ingest backlog",
        "Envelopes in the last hour",
    ):
        assert label in page


def test_an_empty_database_renders_its_empty_states(operator_client):
    """Should render on a fresh install with nothing recorded."""
    page = body(operator_client)

    assert "Nothing is firing" in page
    assert "No issue has been recorded yet" in page


def test_the_firing_list_matches_the_firing_count(operator_client, make_issue):
    """Should not show three rows under a headline that says four."""
    make_issue(title="Live")
    make_issue(title="Muted", triage_state=models.TriageState.IGNORED)
    make_issue(title="Settled", source_state=models.SourceState.RESOLVED)

    response = operator_client.get("/overview/")

    firing = next(kpi for kpi in response.context["kpis"] if kpi.label == "Firing now")

    result = (len(response.context["firing"]), firing.value)
    expected = (2, 2)

    assert result == expected


def test_the_newest_list_leads_with_the_most_recent_arrival(
    operator_client, make_issue
):
    """Should answer what appeared since the last look."""
    now = timezone.now()
    make_issue(title="Ancient", first_seen=now - datetime.timedelta(days=5))
    make_issue(title="Fresh", first_seen=now)

    response = operator_client.get("/overview/")

    result = [row.issue.title for row in response.context["newest"]]
    expected = ["Fresh", "Ancient"]

    assert result == expected


def test_a_row_links_into_the_issue(operator_client, make_issue):
    """Should get the reader one click from the detail page."""
    issue = make_issue()

    assert f'href="/issues/{issue.pk}/"' in body(operator_client)


def test_the_overview_carries_the_sparkline(operator_client, make_issue):
    """Should show the shape beside the count on both lists."""
    issue = make_issue()
    models.HourlyStat.objects.create(issue=issue, hour=timezone.now(), count=3)

    page = body(operator_client)

    assert "3 in 7 days" in page


def test_the_overview_renders(operator_client, make_issue):
    """Should paint every section off the database alone."""
    make_issue()

    response = operator_client.get("/overview/")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected
