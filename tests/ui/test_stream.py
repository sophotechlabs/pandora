import datetime
import http

import pytest
from django.utils import timezone

from pandora.issues import models
from pandora.ui import views

pytestmark = pytest.mark.django_db


def body(client, path="/", **params):
    return client.get(path, params).content.decode()


def rows(response):
    return response.context["rows"]


# default view


def test_the_stream_defaults_to_the_open_issues(operator_client, make_issue):
    """Should open on what a human still owns, not on everything ever recorded."""
    make_issue(title="Open one")
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)

    response = operator_client.get("/")

    result = [row.issue.title for row in rows(response)]
    expected = ["Open one"]

    assert result == expected


def test_the_default_query_is_shown_in_the_search_box(operator_client):
    """Should teach the grammar by showing the query it is already running."""
    assert "is:unresolved" in body(operator_client)


def test_an_empty_query_means_everything(operator_client, make_issue):
    """Should let the All segment turn the default filter off."""
    make_issue(title="Open one")
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)

    response = operator_client.get("/", {"q": ""})

    result = len(rows(response))
    expected = 2

    assert result == expected


def test_a_row_carries_the_issue_a_reader_needs_to_triage(operator_client, make_issue):
    """Should put title, project, labels and state on one line."""
    make_issue(
        title="KubePodCrashLooping: pod is restarting",
        grouping_labels={"alertname": "KubePodCrashLooping", "namespace": "payments"},
        level=models.Level.ERROR,
    )

    page = body(operator_client)

    assert "KubePodCrashLooping: pod is restarting" in page
    assert "namespace=payments" in page
    assert "chip-error" in page
    assert 'class="dot dot-firing"' in page


def test_a_row_falls_back_to_the_culprit_when_grouping_kept_no_labels(
    operator_client, make_issue
):
    """Should still say what the issue is for an SDK group."""
    make_issue(
        title="HTTPError",
        grouping_labels={},
        culprit="listopad.core.transport in get_json",
    )

    assert "listopad.core.transport in get_json" in body(operator_client)


def test_every_row_carries_an_inline_sparkline(operator_client, make_issue):
    """Should draw the seven day shape without a request per row."""
    issue = make_issue()
    models.HourlyStat.objects.create(issue=issue, hour=timezone.now(), count=5)

    page = body(operator_client)

    assert 'aria-label="7 day activity"' in page
    assert "5 in 7 days" in page


def test_the_stream_stays_off_the_per_row_query_path(
    operator_client, make_issue, django_assert_max_num_queries
):
    """Should keep sparklines and durations inside the page query, not per row."""
    for index in range(10):
        make_issue(title=f"Issue {index}")

    with django_assert_max_num_queries(12):
        operator_client.get("/")


# segments


def test_the_segments_count_every_triage_state(operator_client, make_issue):
    """Should let a reader see the shape of the backlog before clicking."""
    make_issue(title="New one")
    make_issue(title="Owned", triage_state=models.TriageState.ACKNOWLEDGED)
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)
    make_issue(title="Muted", triage_state=models.TriageState.IGNORED)

    response = operator_client.get("/")

    result = {segment.key: segment.count for segment in response.context["segments"]}
    expected = {
        "unresolved": 2,
        "new": 1,
        "acknowledged": 1,
        "resolved": 1,
        "ignored": 1,
        "everything": 4,
    }

    assert result == expected


def test_the_open_segment_is_active_by_default(operator_client):
    """Should mark where the reader is."""
    response = operator_client.get("/")

    result = [s.key for s in response.context["segments"] if s.active]
    expected = ["unresolved"]

    assert result == expected


def test_a_hand_written_query_matches_no_segment(operator_client):
    """Should not claim a segment the query is not equal to."""
    response = operator_client.get("/", {"q": "is:unresolved level:error"})

    result = [s.key for s in response.context["segments"] if s.active]
    expected = []

    assert result == expected


def test_extra_spacing_still_matches_a_segment(operator_client):
    """Should not lose the highlight to a stray double space."""
    response = operator_client.get("/", {"q": "  is:new  "})

    result = [s.key for s in response.context["segments"] if s.active]
    expected = ["new"]

    assert result == expected


# rejected terms


def test_an_unknown_filter_is_named_back_to_the_reader(operator_client, make_issue):
    """Should say the term did nothing rather than return a confusing list."""
    make_issue()

    response = operator_client.get("/", {"q": "severity:page"})

    assert response.context["ignored"] == ("severity:page",)
    assert "severity:page" in response.content.decode()


def test_an_unusable_value_is_named_back_to_the_reader(operator_client, make_issue):
    """Should report a term the filter understood but could not use."""
    make_issue()

    response = operator_client.get("/", {"q": "level:catastrophic"})

    result = response.context["ignored"]
    expected = ("level:catastrophic",)

    assert result == expected


# sorting


def test_the_stream_sorts_by_last_seen_by_default(operator_client, make_issue):
    """Should put the issue that moved most recently on top."""
    now = timezone.now()
    make_issue(title="Older", last_seen=now - datetime.timedelta(hours=2))
    make_issue(title="Newer", last_seen=now)

    response = operator_client.get("/")

    result = [row.issue.title for row in rows(response)]
    expected = ["Newer", "Older"]

    assert result == expected


def test_the_stream_can_sort_by_event_count(operator_client, make_issue):
    """Should let a reader find the loudest issue, not the newest."""
    now = timezone.now()
    make_issue(title="Loud", event_count=90, last_seen=now - datetime.timedelta(days=1))
    make_issue(title="Quiet", event_count=2, last_seen=now)

    response = operator_client.get("/", {"sort": "events"})

    result = [row.issue.title for row in rows(response)]
    expected = ["Loud", "Quiet"]

    assert result == expected


def test_the_stream_can_sort_by_first_seen(operator_client, make_issue):
    """Should answer what appeared most recently rather than what fired."""
    now = timezone.now()
    make_issue(title="Ancient", first_seen=now - datetime.timedelta(days=9))
    make_issue(title="Fresh", first_seen=now)

    response = operator_client.get("/", {"sort": "first_seen"})

    result = [row.issue.title for row in rows(response)]
    expected = ["Fresh", "Ancient"]

    assert result == expected


def test_an_unknown_sort_falls_back_to_the_default(operator_client, make_issue):
    """Should not let a hand-edited URL reach the ORM as an ordering."""
    make_issue()

    response = operator_client.get("/", {"sort": "-title; drop table"})

    result = response.context["sort"].key
    expected = "last_seen"

    assert result == expected


# paging


def test_the_stream_pages_at_the_declared_size(operator_client, make_issue):
    """Should keep the page short enough to read."""
    for index in range(views.PAGE_SIZE + 4):
        make_issue(title=f"Issue {index}")

    response = operator_client.get("/")

    result = (len(rows(response)), response.context["total"])
    expected = (views.PAGE_SIZE, views.PAGE_SIZE + 4)

    assert result == expected


def test_the_second_page_holds_the_rest(operator_client, make_issue):
    """Should reach every issue, not only the first screen."""
    for index in range(views.PAGE_SIZE + 4):
        make_issue(title=f"Issue {index}")

    response = operator_client.get("/", {"page": "2"})

    result = len(rows(response))
    expected = 4

    assert result == expected


def test_paging_keeps_the_query_and_the_sort(operator_client, make_issue):
    """Should not drop the filter when the reader clicks Next."""
    for index in range(views.PAGE_SIZE + 1):
        make_issue(title=f"Issue {index}")

    response = operator_client.get("/", {"q": "is:new", "sort": "events"})

    result = response.context["page_query"]
    expected = "q=is%3Anew&sort=events"

    assert result == expected


def test_a_nonsense_page_number_lands_on_the_first_page(operator_client, make_issue):
    """Should not 500 on a hand-edited page parameter."""
    make_issue()

    response = operator_client.get("/", {"page": "banana"})

    result = response.context["page"].number
    expected = 1

    assert result == expected


# empty state and partial rendering


def test_an_empty_stream_explains_the_grammar(operator_client):
    """Should teach the filters at the moment the reader found nothing."""
    page = body(operator_client)

    assert "No issue matches this search" in page
    assert "is, state, level, project, environment, tag, label, seen, age" in page


def test_the_partial_returns_only_the_rows(operator_client, make_issue):
    """Should let the page refresh its table without a reload."""
    make_issue(title="Live one")

    page = body(operator_client, "/", partial="1")

    assert page.strip().startswith('<tbody id="stream-rows"')
    assert "<html" not in page
    assert "Live one" in page


def test_the_partial_carries_the_total_for_the_header(operator_client, make_issue):
    """Should let the refreshed table correct the count above it."""
    make_issue()

    page = body(operator_client, "/", partial="1")

    assert 'data-total="1"' in page


def test_a_tag_filter_does_not_repeat_a_row(operator_client, make_issue):
    """Should join to the tag table without doubling the issue."""
    issue = make_issue(title="Wanted")
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-1", count=4)
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-2", count=2)

    response = operator_client.get("/", {"q": "tag:pod=ledger-1"})

    result = [row.issue.title for row in rows(response)]
    expected = ["Wanted"]

    assert result == expected


def test_the_stream_renders_for_a_reader_with_no_session_state(
    operator_client, make_issue
):
    """Should render the whole page off the database alone."""
    make_issue()

    response = operator_client.get("/")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


# ranking


def test_relevance_puts_this_morning_above_last_month(operator_client, make_issue):
    """Should stop the chatty issue from owning the top of the list forever."""
    from pandora.issues import models as issue_models

    now = timezone.now()
    loud = make_issue(title="Loud last month")
    fresh = make_issue(title="Fresh this morning")
    for day in range(10, 40):
        issue_models.HourlyStat.objects.create(
            issue=loud, hour=now - datetime.timedelta(days=day), count=20
        )
    issue_models.HourlyStat.objects.create(
        issue=fresh, hour=now - datetime.timedelta(hours=1), count=6
    )

    response = operator_client.get("/", {"sort": "relevance", "q": ""})

    result = [row.issue.title for row in response.context["rows"]]

    assert result[0] == "Fresh this morning"


def test_relevance_is_offered_but_is_not_the_default(operator_client, make_issue):
    """Should stay opt-in until its query cost is measured on real volume."""
    make_issue()

    response = operator_client.get("/")

    result = (
        response.context["sort"].key,
        [option.key for option in response.context["sorts"]],
    )

    assert result[0] == "last_seen"
    assert "relevance" in result[1]


def test_spread_ranks_by_how_many_places_are_affected(operator_client, make_issue):
    """Should answer 'is it everyone or one node' from the stream itself."""
    from pandora.issues import models as issue_models

    narrow = make_issue(title="One node")
    wide = make_issue(title="Every node")
    issue_models.TagStat.objects.create(issue=narrow, key="node", value="a", count=9)
    for name in ("a", "b", "c"):
        issue_models.TagStat.objects.create(issue=wide, key="node", value=name, count=1)

    response = operator_client.get("/", {"sort": "breadth", "q": ""})

    result = [row.issue.title for row in response.context["rows"]]

    assert result[0] == "Every node"
