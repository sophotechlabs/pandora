import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.events import types
from pandora.issues import models
from pandora.mcp import tools
from tests.web import fakes

pytestmark = pytest.mark.django_db


@pytest.fixture
def read_token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="mcp",
        token="read-token-value",
        source=core_models.TokenSource.SDK,
        scope=core_models.TokenScope.READ,
    )


@pytest.fixture
def make_issue(project):
    def build(title, **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": title,
            "fingerprint": [title],
            "grouping_labels": {"namespace": "payments"},
            "title": title,
            "culprit": title,
            "level": models.Level.ERROR,
            "environment": "p-mk1",
            "first_seen": timezone.now() - datetime.timedelta(hours=2),
            "last_seen": timezone.now(),
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build


# the token is the boundary


def test_a_read_scoped_token_resolves(read_token):
    """Should accept the credential the JSON API already uses, not invent a second one."""
    result = tools.resolve_token("read-token-value").pk
    expected = read_token.pk

    assert result == expected


def test_an_ingest_token_is_refused(project):
    """Should not let a write credential read — the scopes exist to be honoured."""
    core_models.IngestToken.objects.create(
        project=project,
        name="ingest",
        token="ingest-token",
        scope=core_models.TokenScope.INGEST,
    )

    with pytest.raises(tools.ToolError, match="read-scoped"):
        tools.resolve_token("ingest-token")


def test_a_deactivated_token_is_refused(read_token):
    """Should stop working the moment the token is turned off."""
    read_token.active = False
    read_token.save(update_fields=["active"])

    with pytest.raises(tools.ToolError):
        tools.resolve_token("read-token-value")


def test_an_unknown_token_is_refused():
    """Should say what is wrong without hinting at what a real token looks like."""
    with pytest.raises(tools.ToolError, match="PANDORA_MCP_TOKEN"):
        tools.resolve_token("nope")


# search


def test_search_defaults_to_the_unresolved_queue(read_token, make_issue):
    """Should open where an operator opens — resolved noise is not what an agent is asked about."""
    make_issue("open")
    make_issue("done", triage_state=models.TriageState.RESOLVED)

    result = [row["title"] for row in tools.search_issues(read_token)["results"]]
    expected = ["open"]

    assert result == expected


def test_search_speaks_the_operator_query_language(read_token, make_issue):
    """Should take the same query someone would paste out of the UI."""
    make_issue("warn", level=models.Level.WARNING)
    make_issue("bad")

    found = tools.search_issues(read_token, "is:unresolved level:error")

    result = [row["title"] for row in found["results"]]
    expected = ["bad"]

    assert result == expected


def test_search_names_a_term_it_did_not_understand(read_token, make_issue):
    """Should say so rather than silently returning everything, which reads as a confident wrong answer."""
    make_issue("one")

    found = tools.search_issues(read_token, "nonsense:value")

    result = found["ignored_terms"]
    expected = ["nonsense:value"]

    assert result == expected


def test_search_is_scoped_to_the_token_project(read_token, make_issue):
    """Should never reach across projects, the same rule the HTTP API holds."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    make_issue("mine")
    make_issue("theirs", project=other, fingerprint_hash="theirs")

    result = [row["title"] for row in tools.search_issues(read_token, "")["results"]]
    expected = ["mine"]

    assert result == expected


def test_the_search_limit_is_capped(read_token, make_issue):
    """Should bound what an agent can pull into its context in one call."""
    for index in range(5):
        make_issue(f"issue-{index}")

    result = len(tools.search_issues(read_token, "", limit=10_000)["results"])
    expected = 5

    assert result == expected


def test_a_limit_below_one_still_returns_a_row(read_token, make_issue):
    """Should treat a nonsense limit as one rather than as none."""
    make_issue("one")

    result = len(tools.search_issues(read_token, "", limit=0)["results"])
    expected = 1

    assert result == expected


# one issue


def test_an_issue_carries_its_episodes_and_tags(read_token, make_issue):
    """Should hand over the history in one call, so an agent does not need three."""
    issue = make_issue("alert")
    models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="abc",
        labels={"namespace": "payments"},
        environment="p-mk1",
        starts_at=timezone.now() - datetime.timedelta(hours=1),
    )
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-1", count=3)

    payload = tools.get_issue(read_token, issue.pk)

    result = (
        payload["title"],
        len(payload["episodes"]),
        payload["tag_stats"][0]["value"],
        payload["fingerprint"],
    )
    expected = ("alert", 1, "ledger-1", ["alert"])

    assert result == expected


def test_an_issue_in_another_project_is_not_found(read_token, make_issue):
    """Should refuse by id as well as by listing — otherwise the scope is a filter, not a boundary."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    theirs = make_issue("theirs", project=other, fingerprint_hash="theirs")

    with pytest.raises(tools.ToolError, match="not in project"):
        tools.get_issue(read_token, theirs.pk)


# occurrences


def test_events_come_back_with_their_payload(read_token, make_issue, mocker):
    """Should carry the stack trace, which is the whole reason an agent asks for an occurrence."""
    issue = make_issue("boom")
    event = types.Event(
        id="01J8ZQ7X4N0000000000000001",
        project_id=issue.project_id,
        timestamp=timezone.now(),
        level="error",
        message="boom",
        issue_id=issue.pk,
        source="sdk",
        environment="p-mk1",
        payload={"exceptions": [{"type": "ValueError", "frames": []}]},
    )
    mocker.patch(
        "pandora.mcp.tools.get_store", return_value=fakes.FakeEventStore([event])
    )

    payload = tools.get_issue_events(read_token, issue.pk)

    result = payload["results"][0]["payload"]["exceptions"][0]["type"]
    expected = "ValueError"

    assert result == expected


def test_a_store_without_fetch_reports_that_it_is_unsupported(
    read_token, make_issue, mocker
):
    """Should say so rather than returning an empty list an agent would read as 'no occurrences'."""
    issue = make_issue("boom")
    store = mocker.Mock()
    store.fetch.side_effect = NotImplementedError
    mocker.patch("pandora.mcp.tools.get_store", return_value=store)

    result = tools.get_issue_events(read_token, issue.pk)
    expected = {"supported": False, "results": []}

    assert result == expected


def test_the_event_limit_is_capped(read_token, make_issue, mocker):
    """Should bound the payload — occurrences carry whole stack traces."""
    issue = make_issue("boom")
    events = [
        types.Event(
            id=f"01J8ZQ7X4N{index:022d}",
            project_id=issue.project_id,
            timestamp=timezone.now(),
            level="error",
            message="boom",
            issue_id=issue.pk,
            source="sdk",
            environment="p-mk1",
        )
        for index in range(80)
    ]
    mocker.patch(
        "pandora.mcp.tools.get_store", return_value=fakes.FakeEventStore(events)
    )

    result = len(tools.get_issue_events(read_token, issue.pk, limit=999)["results"])
    expected = tools.EVENT_LIMIT_MAX

    assert result == expected


# markdown


def test_an_issue_renders_as_markdown(read_token, make_issue, mocker):
    """Should hand an agent the same artefact a person would paste."""
    issue = make_issue("PaymentGatewayError")
    mocker.patch("pandora.mcp.tools.get_store", return_value=fakes.FakeEventStore([]))

    result = tools.issue_as_markdown(read_token, issue.pk).splitlines()[0]
    expected = "# PaymentGatewayError"

    assert result == expected


def test_markdown_survives_a_store_that_cannot_fetch(read_token, make_issue, mocker):
    """Should still render on a database that keeps no single occurrences."""
    issue = make_issue("alert")
    store = mocker.Mock()
    store.fetch.side_effect = NotImplementedError
    mocker.patch("pandora.mcp.tools.get_store", return_value=store)

    result = tools.issue_as_markdown(read_token, issue.pk).startswith("# alert")

    assert result is True
