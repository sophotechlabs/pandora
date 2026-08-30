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


# the stack trace


SDK_PAYLOAD = {
    "exceptions": [
        {
            "type": "ConnectionResetError",
            "value": "connection reset by peer",
            "frames": [{"module": "http.client", "function": "read", "in_app": False}],
        },
        {
            "type": "PaymentGatewayError",
            "value": "acquirer refused the authorisation",
            "module": "checkout.errors",
            "mechanism": {"type": "django", "handled": False},
            "frames": [
                {
                    "module": "django.core.handlers",
                    "function": "inner",
                    "filename": "django/core/handlers/base.py",
                    "lineno": 197,
                    "in_app": False,
                },
                {
                    "module": "checkout.gateway",
                    "function": "charge",
                    "filename": "checkout/gateway.py",
                    "lineno": 141,
                    "in_app": True,
                    "pre_context": ["    body = self.body(card)"],
                    "context_line": "    raise PaymentGatewayError(reason)",
                    "post_context": ["", "def refund(self):"],
                    "vars": {"amount": "48250"},
                },
            ],
        },
    ],
    "breadcrumbs": [
        {
            "category": "httplib",
            "level": "info",
            "message": "POST https://acquirer.invalid/v2/authorise",
            "timestamp": 1786000000,
        }
    ],
    "user": {"id": "44182", "username": "renata.k"},
    "request": {"url": "https://shop.test/authorise", "method": "POST"},
    "contexts": {"runtime": {"name": "CPython", "version": "3.12.7"}},
    "release": "checkout@2026.8.3",
}


@pytest.fixture
def sdk_issue(make_issue, stored_events):
    issue = make_issue(title="PaymentGatewayError: checkout.gateway in charge")
    stored_events(
        [
            make_event(
                issue,
                index=1,
                source="sdk",
                message="PaymentGatewayError: acquirer refused the authorisation",
                payload=SDK_PAYLOAD,
            )
        ]
    )
    return issue


def test_the_issue_page_shows_the_latest_stack_trace(operator_client, sdk_issue):
    """Should render the failing frame without the reader opening a tab."""
    page = body(operator_client, sdk_issue)

    result = (
        "Latest event" in page,
        "PaymentGatewayError" in page,
        "checkout.gateway in charge" in page,
        "raise PaymentGatewayError(reason)" in page,
    )
    expected = (True, True, True, True)

    assert result == expected


def test_the_stack_trace_marks_the_failing_line(operator_client, sdk_issue):
    """Should highlight the context line so the eye lands on it."""
    page = body(operator_client, sdk_issue)

    result = 'class="src-line src-current"' in page

    assert result is True


def test_the_stack_trace_numbers_the_source_context(operator_client, sdk_issue):
    """Should let the snippet be matched against the file on disk."""
    page = body(operator_client, sdk_issue)

    result = ('<span class="src-no">140</span>' in page, ">141<" in page)
    expected = (True, True)

    assert result == expected


def test_the_stack_trace_names_the_cause(operator_client, sdk_issue):
    """Should render the chained exception under a Caused by heading."""
    page = body(operator_client, sdk_issue)

    result = ("Caused by" in page, "ConnectionResetError" in page)
    expected = (True, True)

    assert result == expected


def test_an_unhandled_exception_is_labelled(operator_client, sdk_issue):
    """Should say that nothing caught it."""
    page = body(operator_client, sdk_issue)

    result = "unhandled" in page

    assert result is True


def test_the_issue_page_shows_the_breadcrumbs(operator_client, sdk_issue):
    """Should show what the process did before it failed."""
    page = body(operator_client, sdk_issue)

    result = (
        "Breadcrumbs" in page,
        "POST https://acquirer.invalid/v2/authorise" in page,
    )
    expected = (True, True)

    assert result == expected


def test_the_issue_page_shows_the_context_cards(operator_client, sdk_issue):
    """Should show who hit it, what they asked for and what was running."""
    page = body(operator_client, sdk_issue)

    result = (
        "renata.k" in page,
        "https://shop.test/authorise" in page,
        "CPython" in page,
        "checkout@2026.8.3" in page,
    )
    expected = (True, True, True, True)

    assert result == expected


def test_an_alert_issue_shows_no_stack_trace_section(
    operator_client, make_issue, stored_events
):
    """Should leave the page as it was for an occurrence that carries no payload."""
    issue = make_issue()
    stored_events([make_event(issue)])

    page = body(operator_client, issue)

    result = "Latest event" in page

    assert result is False


def test_the_occurrences_tab_renders_the_frames(operator_client, sdk_issue):
    """Should give the same reading of an older occurrence, not only the newest."""
    page = body(operator_client, sdk_issue, tab="occurrences/")

    result = ("checkout.gateway in charge" in page, "Raw payload" in page)
    expected = (True, True)

    assert result == expected


def test_the_page_says_why_the_issue_is_grouped_as_it_is(operator_client, make_issue):
    """Should answer the first question anyone asks about a wrongly-grouped issue."""
    from pandora.issues.models import GroupingSource

    issue = make_issue(grouping_source=GroupingSource.STACK)

    assert "the exception and the frame it came from" in body(operator_client, issue)


def test_an_alertmanager_issue_names_the_rule_that_grouped_it(
    operator_client, make_issue
):
    """Should be one click from the issue to the rule that decided its shape."""
    from pandora.issues.models import GroupingRule, GroupingSource

    rule = GroupingRule.objects.create(priority=10, labels=["pod"])
    issue = make_issue(grouping_source=GroupingSource.RULE, grouping_rule=rule)

    assert f"rule {rule.pk}" in body(operator_client, issue)


def test_an_issue_grouped_before_provenance_existed_says_so(
    operator_client, make_issue
):
    """Should not claim a reason for an issue that predates the column."""
    issue = make_issue(grouping_source="")

    assert "an earlier version of pandora" in body(operator_client, issue)


def test_the_page_shows_what_sets_the_issue_apart(operator_client, make_issue):
    """Should answer 'what is different about this one' from the breakdown on disk."""
    from pandora.issues import models as issue_models

    issue = make_issue()
    other = make_issue()
    issue_models.TagStat.objects.create(
        issue=issue, key="node", value="broken-1", count=9
    )
    issue_models.TagStat.objects.create(
        issue=other, key="node", value="fine-1", count=9
    )

    page = body(operator_client, issue)

    assert "What sets this apart" in page and "node=broken-1" in page


def test_the_page_says_when_the_breakdown_was_sampled(operator_client, make_issue):
    """Should carry the caveat that makes the number trustworthy."""
    from pandora.issues import models as issue_models

    issue = make_issue()
    issue_models.TagStat.objects.create(
        issue=issue, key="node", value="broken-1", count=9
    )
    issue_models.TagStat.objects.create(
        issue=issue, key="node", value=issue_models.TAG_OVERFLOW_VALUE, count=1
    )

    assert "the key filled its cap" in body(operator_client, issue)


def test_an_issue_with_nothing_distinguishing_shows_no_panel(
    operator_client, make_issue
):
    """Should not put an empty card on every issue page."""
    issue = make_issue()

    assert "What sets this apart" not in body(operator_client, issue)
