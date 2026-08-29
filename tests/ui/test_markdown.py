import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.events import types
from pandora.issues import models
from tests.web import fakes

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "exceptions": [
        {
            "type": "ConnectionResetError",
            "value": "connection reset by peer",
            "frames": [{"module": "http.client", "function": "read", "in_app": False}],
        },
        {
            "type": "PaymentGatewayError",
            "value": "acquirer refused the authorisation",
            "mechanism": {"type": "django", "handled": False},
            "frames": [
                {
                    "module": "checkout.gateway",
                    "function": "charge",
                    "filename": "checkout/gateway.py",
                    "lineno": 141,
                    "in_app": True,
                    "context_line": "    raise PaymentGatewayError(reason)",
                }
            ],
        },
    ],
    "breadcrumbs": [
        {
            "category": "httplib",
            "level": "info",
            "message": "POST /authorise",
            "timestamp": 1786000000,
        }
    ],
}


@pytest.fixture
def issue(project):
    return models.Issue.objects.create(
        project=project,
        fingerprint_hash="abc123",
        fingerprint=["checkout.errors", "PaymentGatewayError"],
        grouping_labels={"namespace": "storefront"},
        title="PaymentGatewayError: checkout.gateway in charge",
        culprit="checkout.gateway in charge",
        level=models.Level.ERROR,
        environment="p-mk2",
        first_seen=timezone.now() - datetime.timedelta(hours=2),
        last_seen=timezone.now(),
        event_count=8,
    )


@pytest.fixture
def with_event(issue, mocker):
    event = types.Event(
        id="01J8ZQ7X4N0000000000000001",
        project_id=issue.project_id,
        timestamp=timezone.now(),
        level="error",
        message="PaymentGatewayError: acquirer refused the authorisation",
        issue_id=issue.pk,
        source="sdk",
        environment="p-mk2",
        payload=PAYLOAD,
    )
    mocker.patch(
        "pandora.ui.views.get_store", return_value=fakes.FakeEventStore([event])
    )
    return issue


def body(client, issue):
    return client.get(f"/issues/{issue.pk}/", {"format": "md"})


# the response


def test_markdown_comes_back_as_markdown(operator_client, issue):
    """Should let a browser and a terminal both do something sensible with it."""
    response = body(operator_client, issue)

    result = response["Content-Type"]
    expected = "text/markdown; charset=utf-8"

    assert result == expected


def test_the_filename_names_the_issue(operator_client, issue):
    """Should save as something identifiable rather than the route."""
    response = body(operator_client, issue)

    result = response["Content-Disposition"]
    expected = f'inline; filename="issue-{issue.pk}.md"'

    assert result == expected


def test_markdown_needs_the_same_sign_in_as_the_page(client, issue):
    """Should not become an unauthenticated export of everything the page shows."""
    response = client.get(f"/issues/{issue.pk}/", {"format": "md"})

    result = response.status_code
    expected = 302

    assert result == expected


# what it contains


def test_the_title_and_culprit_lead(operator_client, issue):
    """Should be recognisable in the first two lines, because that is what gets pasted."""
    text = body(operator_client, issue).content.decode()

    result = text.splitlines()[0]
    expected = "# PaymentGatewayError: checkout.gateway in charge"

    assert result == expected


def test_the_facts_table_carries_the_triage_state(operator_client, issue):
    """Should say where the issue stands, which is the first question anyone reading it asks."""
    text = body(operator_client, issue).content.decode()

    result = ("| Triage | New |" in text, "| Project | infrastructure |" in text)
    expected = (True, True)

    assert result == expected


def test_the_stack_trace_is_rendered_as_a_code_block(operator_client, with_event):
    """Should paste into a chat or a ticket without the frames reflowing into prose."""
    text = body(operator_client, with_event).content.decode()

    result = (
        "checkout.gateway in charge — checkout/gateway.py:141" in text,
        "raise PaymentGatewayError(reason)" in text,
        "```" in text,
    )
    expected = (True, True, True)

    assert result == expected


def test_the_cause_is_labelled(operator_client, with_event):
    """Should keep the chain readable — the cause is the half that explains it."""
    text = body(operator_client, with_event).content.decode()

    result = "### Caused by: ConnectionResetError" in text

    assert result is True


def test_application_frames_are_marked(operator_client, with_event):
    """Should show which frames are the reader's own code at a glance."""
    text = body(operator_client, with_event).content.decode()

    result = "> checkout.gateway in charge" in text

    assert result is True


def test_breadcrumbs_are_included(operator_client, with_event):
    """Should carry what happened before the failure, not only the failure."""
    text = body(operator_client, with_event).content.decode()

    result = ("### Breadcrumbs" in text, "POST /authorise" in text)
    expected = (True, True)

    assert result == expected


def test_links_are_rendered_as_markdown_links(operator_client, issue):
    """Should stay clickable wherever the text is pasted."""
    core_models.ServiceLink.objects.create(
        name="Grafana",
        url_template="https://grafana.test/?ns={namespace}",
    )

    text = body(operator_client, issue).content.decode()

    result = "- [Grafana](https://grafana.test/?ns=storefront)" in text

    assert result is True


def test_an_issue_with_no_occurrence_still_renders(operator_client, issue):
    """Should not need an event store to produce something worth pasting."""
    text = body(operator_client, issue).content.decode()

    result = ("# PaymentGatewayError" in text, "## Occurrence" in text)
    expected = (True, False)

    assert result == expected


def test_the_page_offers_the_export(operator_client, issue):
    """Should be findable without knowing the query parameter."""
    page = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    result = 'href="?format=md"' in page

    assert result is True


# the sections built from the issue's own history


def test_episodes_are_rendered_as_a_table(operator_client, issue):
    """Should carry the flap history, which is the part an alert issue is actually about."""
    models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="abc",
        labels={"namespace": "storefront"},
        environment="p-mk2",
        starts_at=timezone.now() - datetime.timedelta(hours=1),
        ends_at=timezone.now(),
    )

    text = body(operator_client, issue).content.decode()

    result = ("## Episodes" in text, "| Started | Ended |" in text)
    expected = (True, True)

    assert result == expected


def test_tags_are_rendered_with_their_counts(operator_client, issue):
    """Should show the breakdown, because 'is it one pod or all of them' is answered there."""
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-1", count=7)

    text = body(operator_client, issue).content.decode()

    result = "- **pod** — ledger-1 (7)" in text

    assert result is True


def test_the_correlated_issues_are_listed(operator_client, issue):
    """Should carry the join into the paste — it is the part nobody can reconstruct from the title."""
    models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="abc",
        labels={"namespace": "storefront"},
        environment="p-mk2",
        starts_at=timezone.now() - datetime.timedelta(hours=2),
    )
    other = models.Issue.objects.create(
        project=issue.project,
        fingerprint_hash="other",
        fingerprint=["other"],
        title="TypeError: renderSummary",
        culprit="renderSummary",
        level=models.Level.ERROR,
        environment="p-mk2",
        first_seen=timezone.now() - datetime.timedelta(hours=1),
        last_seen=timezone.now(),
    )
    models.TagStat.objects.create(
        issue=other, key="namespace", value="storefront", count=1
    )
    models.HourlyStat.objects.create(
        issue=other,
        hour=timezone.now().replace(minute=0, second=0, microsecond=0)
        - datetime.timedelta(hours=1),
        count=40,
    )

    text = body(operator_client, issue).content.decode()

    result = (
        "## Firing in the same window" in text,
        "TypeError: renderSummary" in text,
        "namespace=storefront" in text,
    )
    expected = (True, True, True)

    assert result == expected


def test_activity_is_listed_with_its_actor(operator_client, issue):
    """Should say who changed what, which is what an incident review asks for."""
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.ACKNOWLEDGED,
        actor="renata",
        at=timezone.now(),
        data={"previous_triage_state": "new"},
    )

    text = body(operator_client, issue).content.decode()

    result = ("## Activity" in text, "Acknowledged by renata (was new)" in text)
    expected = (True, True)

    assert result == expected


def test_an_activity_row_with_no_actor_reads_as_pandora(operator_client, issue):
    """Should name the tool for the transitions nobody made by hand."""
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.REGRESSION,
        actor="",
        at=timezone.now(),
    )

    text = body(operator_client, issue).content.decode()

    result = "by pandora" in text

    assert result is True


# bounds


def test_a_long_stack_is_cut_and_says_so(operator_client, issue, mocker):
    """Should stay pasteable — a two-hundred frame trace is not a message anyone reads."""
    frames = [
        {"module": f"mod{index}", "function": "call", "in_app": False}
        for index in range(40)
    ]
    event = types.Event(
        id="01J8ZQ7X4N0000000000000009",
        project_id=issue.project_id,
        timestamp=timezone.now(),
        level="error",
        message="boom",
        issue_id=issue.pk,
        source="sdk",
        environment="p-mk2",
        payload={"exceptions": [{"type": "E", "frames": frames}]},
    )
    mocker.patch(
        "pandora.ui.views.get_store", return_value=fakes.FakeEventStore([event])
    )

    text = body(operator_client, issue).content.decode()

    result = "_28 more frames_" in text

    assert result is True


def test_an_occurrence_without_interfaces_still_renders(operator_client, issue, mocker):
    """Should handle an Alertmanager occurrence, which has a message and no stack trace."""
    event = types.Event(
        id="01J8ZQ7X4N0000000000000010",
        project_id=issue.project_id,
        timestamp=timezone.now(),
        level="error",
        message="TargetDown: scrape target unreachable",
        issue_id=issue.pk,
        source="am",
        environment="p-mk2",
    )
    mocker.patch(
        "pandora.ui.views.get_store", return_value=fakes.FakeEventStore([event])
    )

    text = body(operator_client, issue).content.decode()

    result = (
        "## Occurrence 01J8ZQ7X4N0000000000000010" in text,
        "TargetDown: scrape target unreachable" in text,
    )
    expected = (True, True)

    assert result == expected


def test_a_store_that_cannot_fetch_is_not_an_error(operator_client, issue, mocker):
    """Should still export on a database whose store keeps no single occurrences."""
    store = mocker.Mock()
    store.fetch.side_effect = NotImplementedError
    mocker.patch("pandora.ui.views.get_store", return_value=store)

    result = body(operator_client, issue).status_code
    expected = 200

    assert result == expected


def test_an_issue_without_a_culprit_renders_only_its_title(operator_client, project):
    """Should not leave an empty code span where the culprit would be."""
    bare = models.Issue.objects.create(
        project=project,
        fingerprint_hash="bare",
        fingerprint=["bare"],
        title="Something happened",
        culprit="",
        level=models.Level.WARNING,
        environment="p-mk2",
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )

    text = body(operator_client, bare).content.decode()

    result = text.splitlines()[:3]
    expected = ["# Something happened", "", "| | |"]

    assert result == expected


def test_an_exception_with_no_frames_still_names_itself(operator_client, issue, mocker):
    """Should render an exception a client sent without a stack trace rather than an empty block."""
    event = types.Event(
        id="01J8ZQ7X4N0000000000000011",
        project_id=issue.project_id,
        timestamp=timezone.now(),
        level="error",
        message="boom",
        issue_id=issue.pk,
        source="sdk",
        environment="p-mk2",
        payload={"exceptions": [{"type": "ValueError", "value": "bad input"}]},
    )
    mocker.patch(
        "pandora.ui.views.get_store", return_value=fakes.FakeEventStore([event])
    )

    text = body(operator_client, issue).content.decode()

    result = ("### ValueError" in text, "bad input" in text, "```" in text)
    expected = (True, True, False)

    assert result == expected
