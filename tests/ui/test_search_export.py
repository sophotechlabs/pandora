import csv
import io

import pytest

from pandora.issues import environments

pytestmark = pytest.mark.django_db


def titles(response):
    return [row.issue.title for row in response.context["rows"]]


# free text over what characterises the issue


def test_free_text_still_matches_the_title(operator_client, make_issue):
    """Should not have regressed the search people already use."""
    make_issue(title="PaymentGatewayError on checkout")
    make_issue(title="Something else")

    result = titles(operator_client.get("/", {"q": "gateway"}))
    expected = ["PaymentGatewayError on checkout"]

    assert result == expected


def test_free_text_matches_a_stored_frame_path(operator_client, make_issue):
    """Should find the issue about charge.py by typing charge.py."""
    wanted = make_issue(title="One")
    make_issue(title="Two")
    type(wanted).objects.filter(pk=wanted.pk).update(
        search_text="src/payments/charge.py\ncharge"
    )

    result = titles(operator_client.get("/", {"q": "charge.py"}))
    expected = ["One"]

    assert result == expected


def test_free_text_matches_a_stored_message(operator_client, make_issue):
    """Should reach the words the SDK sent, not only the derived title."""
    wanted = make_issue(title="One")
    make_issue(title="Two")
    type(wanted).objects.filter(pk=wanted.pk).update(
        search_text="connection pool exhausted after 30s"
    )

    result = titles(operator_client.get("/", {"q": "pool exhausted"}))
    expected = ["One"]

    assert result == expected


def test_an_issue_is_listed_once_however_many_fields_match(operator_client, make_issue):
    """Should be one row, not one per matching column."""
    wanted = make_issue(title="charge failed")
    type(wanted).objects.filter(pk=wanted.pk).update(search_text="charge failed")

    result = titles(operator_client.get("/", {"q": "charge"}))
    expected = ["charge failed"]

    assert result == expected


# what the ingest path stores


@pytest.mark.django_db
def test_the_search_column_carries_the_frames(project):
    """Should be filled at ingest, not computed at read time."""
    from pandora.issues import models as issue_models
    from tests.ingest.test_sdk_processor import deliver, event_payload

    deliver(
        project,
        event_payload(
            exception={
                "values": [
                    {
                        "type": "ValueError",
                        "value": "bad",
                        "stacktrace": {
                            "frames": [{"filename": "src/payments/charge.py"}]
                        },
                    }
                ]
            }
        ),
    )

    result = issue_models.Issue.objects.get().search_text

    assert "src/payments/charge.py" in result


# csv


def test_the_export_carries_the_documented_columns(operator_client, make_issue):
    """Should be a stable shape someone can build a script against."""
    make_issue(title="One")

    response = operator_client.get("/", {"csv": "1", "q": ""})
    rows = list(csv.reader(io.StringIO(response.content.decode())))

    result = rows[0]
    expected = [
        "id",
        "project",
        "title",
        "culprit",
        "level",
        "triage_state",
        "source_state",
        "environments",
        "event_count",
        "first_seen",
        "last_seen",
        "fingerprint_hash",
    ]

    assert result == expected


def test_the_export_holds_the_rows_the_query_selected(operator_client, make_issue):
    """Should export the search, not the whole table."""
    make_issue(title="Wanted")
    make_issue(title="Other")

    response = operator_client.get("/", {"csv": "1", "q": "Wanted"})
    rows = list(csv.reader(io.StringIO(response.content.decode())))

    result = [row[2] for row in rows[1:]]
    expected = ["Wanted"]

    assert result == expected


def test_the_export_names_every_environment(operator_client, make_issue):
    """Should carry the same spread the UI shows."""
    issue = make_issue(title="One", environment="p-mk1")
    environments.record(issue, "p-mk2", issue.last_seen)

    response = operator_client.get("/", {"csv": "1", "q": ""})
    rows = list(csv.reader(io.StringIO(response.content.decode())))

    result = rows[1][7]
    expected = "p-mk1 p-mk2"

    assert result == expected


def test_the_export_is_offered_as_a_download(operator_client, make_issue):
    """Should land in a file rather than in the browser window."""
    make_issue()

    response = operator_client.get("/", {"csv": "1"})

    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Content-Type"] == "text/csv"


def test_the_stream_offers_the_export(operator_client, make_issue):
    """Should be findable without knowing the parameter."""
    make_issue()

    body = operator_client.get("/").content.decode()

    assert "Export CSV" in body
