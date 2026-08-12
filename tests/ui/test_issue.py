import datetime
import http

import pytest
from django.utils import timezone

from pandora.events import types
from pandora.issues import models
from pandora.ui import presenters
from tests.web import fakes

pytestmark = pytest.mark.django_db


@pytest.fixture
def stored_events(mocker):
    def install(events):
        store = fakes.FakeEventStore(events)
        mocker.patch("pandora.ui.views.get_store", return_value=store)
        return store

    return install


def make_event(issue, index=1, **overrides):
    fields = {
        "id": f"01J8ZQ7X4N{index:022d}",
        "project_id": issue.project_id,
        "timestamp": timezone.now(),
        "level": "error",
        "message": f"occurrence {index}",
        "issue_id": issue.pk,
        "tags": {"namespace": "payments"},
        "extra": {"generatorURL": "https://prometheus.test/graph"},
        "source": "am",
        "environment": "p-mk1",
    }
    fields.update(overrides)
    return types.Event(**fields)


def body(client, issue, tab="", **params):
    return client.get(f"/issues/{issue.pk}/{tab}", params).content.decode()


# the page


def test_the_issue_page_leads_with_the_issue(operator_client, make_issue):
    """Should name the issue, its level and its triage state at the top."""
    issue = make_issue(
        title="TargetDown: scrape target unreachable",
        level=models.Level.WARNING,
    )

    page = body(operator_client, issue)

    assert "TargetDown: scrape target unreachable" in page
    assert "chip-warning" in page
    assert "pill-new" in page


def test_the_issue_page_reports_the_counters(operator_client, make_issue):
    """Should answer how loud, how long and how recent without a click."""
    issue = make_issue(event_count=42, open_episode_count=1)

    page = body(operator_client, issue)

    assert "Events" in page
    assert "42" in page
    assert "Open episodes" in page


def test_a_firing_issue_reports_how_long_it_has_been_open(
    operator_client, make_issue, project
):
    """Should measure from the earliest open episode."""
    issue = make_issue()
    now = timezone.now()
    models.Episode.objects.create(
        project=project,
        issue=issue,
        am_fingerprint="a" * 16,
        starts_at=now - datetime.timedelta(hours=3),
        ends_at=None,
        last_delivery_at=now,
    )

    response = operator_client.get(f"/issues/{issue.pk}/")

    result = response.context["row"].duration
    expected = "3h 0m"

    assert result == expected


def test_the_chart_covers_thirty_days(operator_client, make_issue):
    """Should give the detail page a longer window than the row sparkline."""
    issue = make_issue()
    models.HourlyStat.objects.create(issue=issue, hour=timezone.now(), count=4)

    response = operator_client.get(f"/issues/{issue.pk}/")

    result = len(response.context["chart"])
    expected = presenters.CHART_BUCKETS

    assert result == expected


def test_the_chart_labels_each_bucket_with_its_count(operator_client, make_issue):
    """Should let a reader hover one bar and read the number behind it."""
    issue = make_issue()
    models.HourlyStat.objects.create(issue=issue, hour=timezone.now(), count=4)

    response = operator_client.get(f"/issues/{issue.pk}/")

    result = response.context["chart"][-1].label.endswith("— 4")
    expected = True

    assert result == expected


def test_an_hour_outside_the_window_is_not_charted(operator_client, make_issue):
    """Should not fold a two-month-old spike into today's first bucket."""
    issue = make_issue()
    models.HourlyStat.objects.create(
        issue=issue,
        hour=timezone.now() - datetime.timedelta(days=60),
        count=99,
    )

    response = operator_client.get(f"/issues/{issue.pk}/")

    result = sum(
        int(column.label.rsplit("— ", 1)[1]) for column in response.context["chart"]
    )
    expected = 0

    assert result == expected


def test_a_missing_issue_is_a_not_found(operator_client):
    """Should 404 rather than render an empty page."""
    response = operator_client.get("/issues/999999/")

    result = response.status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


def test_an_unknown_tab_is_a_not_found(operator_client, make_issue):
    """Should not let a hand-edited URL pick a template."""
    issue = make_issue()

    response = operator_client.get(f"/issues/{issue.pk}/passwd/")

    result = response.status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


# tabs


def test_the_episode_tab_lists_the_episodes(operator_client, make_issue, project):
    """Should show the firing history the Alertmanager door recorded."""
    issue = make_issue()
    now = timezone.now()
    models.Episode.objects.create(
        project=project,
        issue=issue,
        am_fingerprint="3c1f6a2b9d4e5087",
        labels={"pod": "ledger-7d9f4c8b6d-hk2mp"},
        starts_at=now - datetime.timedelta(hours=2),
        ends_at=None,
        delivery_count=2,
        last_delivery_at=now,
    )

    page = body(operator_client, issue, "episodes/")

    assert "pod=ledger-7d9f4c8b6d-hk2mp" in page
    assert "firing" in page
    assert "Newest 1 episodes" in page


def test_the_episode_tab_says_when_there_are_none(operator_client, make_issue):
    """Should explain the empty tab for an SDK-only issue."""
    issue = make_issue()

    assert "No episodes recorded" in body(operator_client, issue, "episodes/")


def test_the_tag_tab_breaks_down_the_values(operator_client, make_issue):
    """Should show which value inside the group dominates."""
    issue = make_issue()
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-1", count=9)
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-2", count=3)

    page = body(operator_client, issue, "tags/")

    assert "ledger-1" in page
    assert "bar-fill" in page


def test_the_tag_tab_says_when_there_are_none(operator_client, make_issue):
    """Should not render an empty chart area."""
    issue = make_issue()

    assert "No tags recorded" in body(operator_client, issue, "tags/")


def test_the_activity_tab_shows_the_audit_trail(operator_client, make_issue):
    """Should say who changed the state and when."""
    issue = make_issue()
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.ACKNOWLEDGED,
        actor="operator",
        at=timezone.now(),
        data={"previous_triage_state": "new"},
    )

    page = body(operator_client, issue, "activity/")

    assert "Acknowledged" in page
    assert "operator" in page
    assert "was new" in page


def test_the_activity_tab_says_when_there_is_none(operator_client, make_issue):
    """Should render the empty feed rather than nothing at all."""
    issue = make_issue()

    assert "No activity recorded" in body(operator_client, issue, "activity/")


def test_a_tab_can_be_fetched_on_its_own(operator_client, make_issue):
    """Should let the page swap a tab without reloading the chrome."""
    issue = make_issue()

    page = body(operator_client, issue, "tags/", partial="1")

    assert "<html" not in page
    assert "No tags recorded" in page


# occurrences


def test_the_occurrence_tab_lists_stored_events(
    operator_client, make_issue, stored_events
):
    """Should show the individual events behind the group."""
    issue = make_issue()
    stored_events([make_event(issue, 1, message="ledger timed out")])

    page = body(operator_client, issue)

    assert "ledger timed out" in page
    assert "namespace=payments" in page


def test_an_occurrence_carries_its_whole_payload(
    operator_client, make_issue, stored_events
):
    """Should let a reader open one event and see everything pandora kept."""
    issue = make_issue()
    stored_events([make_event(issue, 1)])

    page = body(operator_client, issue)

    assert "generatorURL" in page
    assert "https://prometheus.test/graph" in page


def test_the_occurrence_tab_only_asks_for_this_issue(
    operator_client, make_issue, stored_events
):
    """Should scope the store read to the issue and its project."""
    issue = make_issue()
    store = stored_events([make_event(issue, 1)])

    operator_client.get(f"/issues/{issue.pk}/")

    result = (store.calls[0]["issue_id"], store.calls[0]["project_id"])
    expected = (issue.pk, issue.project_id)

    assert result == expected


def test_the_occurrence_tab_offers_the_next_page(
    operator_client, make_issue, stored_events
):
    """Should page rather than render every event pandora holds."""
    issue = make_issue()
    events = [make_event(issue, index) for index in range(presenters.EVENT_ID_HEAD * 5)]
    stored_events(events)

    response = operator_client.get(f"/issues/{issue.pk}/")

    result = response.context["events"].next_cursor is not None
    expected = True

    assert result == expected


def test_the_occurrence_page_starts_where_the_cursor_says(
    operator_client, make_issue, stored_events
):
    """Should let Older occurrences walk back through the store."""
    issue = make_issue()
    store = stored_events([make_event(issue, 1)])

    operator_client.get(f"/issues/{issue.pk}/", {"cursor": "01J8ZQ7X4N9"})

    result = store.calls[0]["before"]
    expected = "01J8ZQ7X4N9"

    assert result == expected


def test_the_occurrence_tab_says_when_nothing_is_stored(
    operator_client, make_issue, stored_events
):
    """Should point an Alertmanager reader at the Episodes tab instead."""
    issue = make_issue()
    stored_events([])

    assert "No stored occurrence" in body(operator_client, issue)


def test_a_store_without_a_reader_says_so_rather_than_failing(
    operator_client, make_issue, mocker
):
    """Should degrade to a sentence when the backend cannot list events."""
    issue = make_issue()
    mocker.patch(
        "pandora.ui.views.get_store",
        return_value=fakes.UnbuiltEventStore(),
    )

    page = body(operator_client, issue)

    assert "no event store" in page
