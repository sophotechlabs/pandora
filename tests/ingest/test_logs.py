import http
import json

import pytest

from pandora.core import models as core_models
from pandora.events.store import get_store
from pandora.ingest import models as ingest_models
from pandora.ingest.translators import logs
from pandora.issues import models as issue_models
from pandora.scrub.models import DropRule

pytestmark = pytest.mark.django_db

TRACE = """Traceback (most recent call last):
  File "/app/charge.py", line 42, in charge
    gateway.send(order)
GatewayError: declined"""


@pytest.fixture
def key(project):
    return core_models.DsnKey.objects.create(project=project, public_key="l" * 32)


@pytest.fixture
def send(client, key):
    def post(rows):
        body = "\n".join(json.dumps(row) for row in rows).encode()
        return client.post(
            f"/api/{key.project_id}/logs/?sentry_key={key.public_key}",
            data=body,
            content_type="application/x-ndjson",
        )

    return post


# the translation


def test_a_plain_line_becomes_an_event():
    """Should take the shape every shipper already emits."""
    result = logs.to_event({"message": "connection refused", "level": "error"})

    assert result["logentry"]["formatted"] == "connection refused"
    assert result["level"] == issue_models.Level.ERROR


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("warn", issue_models.Level.WARNING),
        ("WARNING", issue_models.Level.WARNING),
        ("info", issue_models.Level.INFO),
        ("debug", issue_models.Level.DEBUG),
        ("fatal", issue_models.Level.FATAL),
        ("panic", issue_models.Level.FATAL),
        ("nonsense", issue_models.Level.ERROR),
    ],
)
def test_every_spelling_of_a_level_is_understood(sent, expected):
    """Should not need one shipper's vocabulary — they all differ."""
    result = logs.to_event({"msg": "x", "level": sent})["level"]

    assert result == expected


def test_the_message_is_found_under_any_of_its_names():
    """Should read `msg`, `log` and `body` as well as `message`."""
    result = logs.to_event({"msg": "hello"})["logentry"]["formatted"]
    expected = "hello"

    assert result == expected


def test_a_stack_trace_becomes_an_exception():
    """Should be the whole reason to take logs — a trace makes a real issue."""
    result = logs.to_event({"message": "boom", "stack": TRACE})

    exception = result["exception"]["values"][0]

    assert exception["type"] == "GatewayError"
    assert exception["stacktrace"]["frames"][0]["filename"] == "/app/charge.py"


def test_a_nested_stack_is_found():
    """Should reach the shape a structured logger nests it in."""
    result = logs.to_event({"message": "boom", "exception": {"stacktrace": TRACE}})

    assert result["exception"]["values"][0]["type"] == "GatewayError"


def test_an_error_kind_without_a_trace_still_makes_an_exception():
    """Should be Datadog's rule — `error.kind` is enough to be an issue."""
    result = logs.to_event({"message": "boom", "error.kind": "TimeoutError"})

    assert result["exception"]["values"][0]["type"] == "TimeoutError"


def test_a_line_with_no_error_shape_carries_no_exception():
    """Should not invent an exception from an ordinary log line."""
    result = logs.to_event({"message": "started"})

    assert "exception" not in result


def test_service_and_host_become_tags():
    """Should give the issue the breakdown the UI already renders."""
    result = logs.to_event({"message": "x", "service": "gateway", "host": "node-1"})

    assert result["tags"]["service"] == "gateway"
    assert result["tags"]["host"] == "node-1"


def test_declared_tags_are_kept():
    """Should let a shipper add its own without a mapping."""
    result = logs.to_event({"message": "x", "tags": {"tenant": "acme"}})["tags"]

    assert result["tenant"] == "acme"


def test_the_tag_count_is_bounded():
    """Should not let a wide log line explode the tag breakdown."""
    row = {"message": "x", "tags": {f"k{index}": index for index in range(200)}}

    result = len(logs.to_event(row)["tags"])

    assert result <= logs.TAG_LIMIT


def test_environment_and_release_are_carried_through():
    """Should let a log line take part in releases like an SDK event does."""
    result = logs.to_event({"message": "x", "env": "p-mk1", "version": "1.2.3"})

    assert result["environment"] == "p-mk1"
    assert result["release"] == "1.2.3"


# parsing the body


def test_blank_lines_are_skipped():
    """Should survive the trailing newline every writer adds."""
    result = logs.parse_lines(b'{"message": "a"}\n\n{"message": "b"}\n')

    assert len(result) == 2


def test_a_line_that_is_not_json_is_refused():
    """Should name the problem rather than swallow the batch."""
    with pytest.raises(logs.LogError, match="not valid JSON"):
        logs.parse_lines(b'{"message": "a"}\nnot json')


def test_a_line_that_is_not_an_object_is_refused():
    """Should be one object per line, which is what NDJSON means."""
    with pytest.raises(logs.LogError, match="JSON object"):
        logs.parse_lines(b"[1, 2, 3]")


# the door


def test_the_door_takes_a_batch(send):
    """Should be a POST and a page of config, not an agent to install."""
    response = send([{"message": "a"}, {"message": "b"}])

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 2)

    assert result == expected


def test_the_door_reports_what_it_took(send):
    """Should let a shipper see its batch was accepted whole."""
    response = send([{"message": "a"}, {"message": "b"}])

    result = response.json()
    expected = {"accepted": 2, "received": 2}

    assert result == expected


def test_a_log_line_becomes_an_issue(send):
    """Should reuse grouping, triage and the whole UI unchanged."""
    send([{"message": "connection refused", "level": "error"}])

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_log_envelope_is_marked_as_coming_from_a_log(send):
    """Should be distinguishable from an SDK event in the ingest page."""
    send([{"message": "a"}])

    result = ingest_models.RawEnvelope.objects.get().source
    expected = core_models.TokenSource.LOG

    assert result == expected


def test_a_stored_log_event_keeps_its_source(send, project):
    send([{"message": "a"}])
    issue = issue_models.Issue.objects.get()

    result = get_store().fetch(project.pk, issue_id=issue.pk)[0].source

    assert result == core_models.TokenSource.LOG


def test_a_dropped_log_is_accounted_as_a_log(send, mocker):
    rule = DropRule.objects.create(name="noisy", field="message", pattern="^skip$")
    record = mocker.patch("pandora.ingest.views.scrub.record_drop")

    send([{"message": "skip"}])

    record.assert_called_once_with(rule, core_models.TokenSource.LOG)


def test_a_trace_in_a_log_line_reaches_the_issue(send):
    """Should end with a real stack trace on the issue page."""
    send([{"message": "boom", "stack": TRACE, "level": "error"}])

    result = issue_models.Issue.objects.get().title

    assert "GatewayError" in result


def test_an_unknown_key_is_refused(client, key):
    """Should sit behind the same DSN key as every other door."""
    response = client.post(
        f"/api/{key.project_id}/logs/?sentry_key={'z' * 32}",
        data=b'{"message": "a"}',
        content_type="application/x-ndjson",
    )

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_a_get_is_refused(client, key):
    """Should be a POST like every other door."""
    result = client.get(f"/api/{key.project_id}/logs/").status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_a_malformed_batch_is_refused(client, key):
    """Should tell the shipper which line broke rather than take half of it."""
    response = client.post(
        f"/api/{key.project_id}/logs/?sentry_key={key.public_key}",
        data=b"not json",
        content_type="application/x-ndjson",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_the_batch_size_is_bounded(send):
    """Should not let one POST become ten thousand envelopes."""
    response = send([{"message": str(index)} for index in range(600)])

    result = response.json()["accepted"]
    expected = 500

    assert result == expected


# otlp


OTLP = {
    "resourceLogs": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "gateway"}},
                    {
                        "key": "deployment.environment",
                        "value": {"stringValue": "p-mk1"},
                    },
                ]
            },
            "scopeLogs": [
                {
                    "scope": {"name": "payments"},
                    "logRecords": [
                        {
                            "timeUnixNano": "1756512000000000000",
                            "severityNumber": 17,
                            "body": {"stringValue": "charge failed"},
                            "attributes": [
                                {
                                    "key": "exception.type",
                                    "value": {"stringValue": "GatewayError"},
                                },
                                {
                                    "key": "exception.stacktrace",
                                    "value": {"stringValue": TRACE},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}


def test_an_otlp_record_becomes_a_row():
    """Should flatten into the same shape the NDJSON door already takes."""
    rows = logs.from_otlp(OTLP)

    result = (len(rows), rows[0]["message"])
    expected = (1, "charge failed")

    assert result == expected


def test_otlp_resource_attributes_reach_the_row():
    """Should carry the service and environment every OTel exporter sets."""
    row = logs.from_otlp(OTLP)[0]

    assert row["service.name"] == "gateway"
    assert row["deployment.environment"] == "p-mk1"


def test_an_otlp_severity_number_becomes_a_level():
    """Should read the number when there is no text, which is the common case."""
    result = logs.from_otlp(OTLP)[0]["level"]
    expected = "error"

    assert result == expected


def test_severity_text_wins_over_the_number():
    """Should prefer what the exporter said in words."""
    document = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {"severityNumber": 9, "severityText": "FATAL", "body": "x"}
                        ]
                    }
                ]
            }
        ]
    }

    result = logs.from_otlp(document)[0]["level"]
    expected = "FATAL"

    assert result == expected


def test_an_otlp_exception_becomes_a_stack():
    """Should be the whole reason to take `/v1/logs` — a trace makes an issue."""
    row = logs.from_otlp(OTLP)[0]
    event = logs.to_event(row)

    result = event["exception"]["values"][0]["type"]
    expected = "GatewayError"

    assert result == expected


def test_an_empty_otlp_document_yields_nothing():
    """Should not raise on a heartbeat request with no records."""
    result = logs.from_otlp({})

    assert result == []


def test_the_otlp_door_takes_a_request(client, key):
    """Should be a second entry point into one translator, not a second pipeline."""
    response = client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={key.public_key}",
        data=json.dumps(OTLP),
        content_type="application/json",
    )

    result = (response.status_code, response.json()["accepted"])
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_the_otlp_door_keeps_otlp_as_the_source(client, key):
    client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={key.public_key}",
        data=json.dumps(OTLP),
        content_type="application/json",
    )
    envelope = ingest_models.RawEnvelope.objects.get()
    issue = issue_models.Issue.objects.get()
    event = get_store().fetch(key.project_id, issue_id=issue.pk)[0]

    assert envelope.source == core_models.TokenSource.OTLP
    assert event.source == core_models.TokenSource.OTLP


def test_the_otlp_door_refuses_an_unknown_key(client, key):
    """Should sit behind the same key as every other door."""
    response = client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={'z' * 32}",
        data=json.dumps(OTLP),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_the_otlp_door_refuses_a_get(client, key):
    """Should be a POST like every other door."""
    result = client.get(f"/api/{key.project_id}/integration/otlp/v1/logs").status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_the_otlp_door_refuses_a_body_that_is_not_json(client, key):
    """Should say so rather than accept nothing quietly."""
    response = client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={key.public_key}",
        data=b"not json",
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_the_otlp_door_refuses_a_non_object(client, key):
    """Should name the shape it wanted."""
    response = client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={key.public_key}",
        data=json.dumps([1, 2]),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


# the awkward shapes


def test_a_stack_that_parses_to_nothing_leaves_no_exception():
    """Should not attach an empty exception when the text was not a trace."""
    result = logs.to_event({"message": "x", "stack": "just some words"})

    assert "exception" not in result


def test_a_nested_stack_under_stack_is_found():
    """Should reach both spellings a structured logger uses."""
    result = logs.to_event({"message": "x", "exception": {"stack": TRACE}})

    assert result["exception"]["values"][0]["type"] == "GatewayError"


def test_a_nested_value_that_is_not_a_string_is_skipped():
    """Should not turn a dict into a trace by stringifying it."""
    result = logs.to_event({"message": "x", "exception": {"stacktrace": {"a": 1}}})

    assert "exception" not in result


def test_a_missing_level_is_an_error():
    """Should assume the line is worth an issue when it says nothing."""
    result = logs.to_event({"message": "x"})["level"]
    expected = issue_models.Level.ERROR

    assert result == expected


def test_a_none_tag_value_is_skipped():
    """Should not write the string None into a tag breakdown."""
    result = logs.to_event({"message": "x", "service": None})["tags"]

    assert "service" not in result


def test_the_service_tag_key_loses_its_dot():
    """Should keep tag keys queryable in the UI's `tag:` filter."""
    result = logs.to_event({"message": "x", "service.name": "gateway"})["tags"]

    assert result["service_name"] == "gateway"


def test_an_otlp_array_attribute_is_flattened():
    """Should render a list attribute as something a tag can hold."""
    document = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "tags",
                            "value": {
                                "arrayValue": {
                                    "values": [
                                        {"stringValue": "a"},
                                        {"stringValue": "b"},
                                    ]
                                }
                            },
                        }
                    ]
                },
                "scopeLogs": [{"logRecords": [{"body": "x"}]}],
            }
        ]
    }

    result = logs.from_otlp(document)[0]["tags"]
    expected = "a, b"

    assert result == expected


def test_an_otlp_record_with_no_timestamp_carries_none():
    """Should not invent a time the exporter did not send."""
    document = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"body": "x"}]}]}]}

    result = logs.from_otlp(document)[0]

    assert "timestamp" not in result


def test_an_otlp_observed_time_is_used_when_there_is_no_event_time():
    """Should take the collector's time rather than none at all."""
    document = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {"body": "x", "observedTimeUnixNano": "1756512000000000000"}
                        ]
                    }
                ]
            }
        ]
    }

    result = logs.from_otlp(document)[0]["timestamp"]

    assert result.startswith("20")


def test_an_otlp_timestamp_that_is_not_a_number_is_dropped():
    """Should not raise on an exporter that sent nonsense."""
    document = {
        "resourceLogs": [
            {"scopeLogs": [{"logRecords": [{"body": "x", "timeUnixNano": "soon"}]}]}
        ]
    }

    result = logs.from_otlp(document)[0]["timestamp"]
    expected = ""

    assert result == expected


def test_an_out_of_range_otlp_timestamp_is_dropped():
    document = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {"logRecords": [{"body": "x", "timeUnixNano": "1" + "0" * 100}]}
                ]
            }
        ]
    }

    result = logs.from_otlp(document)[0]["timestamp"]

    assert result == ""


def test_an_otlp_attribute_with_no_key_is_skipped():
    """Should ignore a malformed attribute rather than key it on the empty string."""
    document = {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"value": {"stringValue": "x"}}]},
                "scopeLogs": [{"logRecords": [{"body": "y"}]}],
            }
        ]
    }

    result = logs.from_otlp(document)[0]

    assert "" not in result


def test_an_otlp_int_attribute_is_read():
    """Should take every scalar shape the protocol defines."""
    document = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [{"key": "port", "value": {"intValue": 8080}}]
                },
                "scopeLogs": [{"logRecords": [{"body": "x"}]}],
            }
        ]
    }

    result = logs.from_otlp(document)[0]["port"]
    expected = "8080"

    assert result == expected


def test_an_otlp_scope_name_becomes_the_logger():
    """Should carry the instrumentation scope, which is what grouping uses."""
    document = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {"scope": {"name": "payments"}, "logRecords": [{"body": "x"}]}
                ]
            }
        ]
    }

    result = logs.from_otlp(document)[0]["logger"]
    expected = "payments"

    assert result == expected


def test_an_otlp_severity_number_outside_the_table_is_an_error():
    """Should not drop a record because the exporter used a reserved number."""
    document = {
        "resourceLogs": [
            {"scopeLogs": [{"logRecords": [{"body": "x", "severityNumber": 99}]}]}
        ]
    }

    result = logs.from_otlp(document)[0]["level"]
    expected = "error"

    assert result == expected


def test_an_otlp_record_with_no_severity_is_an_error():
    """Should assume it is worth an issue when nothing said otherwise."""
    document = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"body": "x"}]}]}]}

    result = logs.from_otlp(document)[0]["level"]
    expected = "error"

    assert result == expected


def test_a_line_over_the_item_limit_is_dropped(client, key, settings):
    """Should refuse one huge line without failing the batch."""
    settings.PANDORA_INGEST_MAX_BYTES = 4 * 1024 * 1024
    body = "\n".join(
        [
            json.dumps({"message": "a"}),
            json.dumps({"message": "x" * (2 * 1024 * 1024)}),
        ]
    ).encode()

    response = client.post(
        f"/api/{key.project_id}/logs/?sentry_key={key.public_key}",
        data=body,
        content_type="application/x-ndjson",
    )

    result = response.json()["accepted"]
    expected = 1

    assert result == expected


def test_an_exception_with_no_module_carries_none():
    """Should not put an empty module on a Node or Go exception."""
    node = "TypeError: bad\n    at f (/a.js:1:2)"

    result = logs.to_event({"message": "x", "stack": node})["exception"]["values"][0]

    assert "module" not in result


def test_an_exception_with_no_frames_still_has_a_type():
    """Should keep the class even when the trace was only its first line."""
    result = logs.to_event({"message": "x", "error.kind": "TimeoutError"})

    assert "stacktrace" not in result["exception"]["values"][0]


def test_the_shipper_tag_loop_stops_at_the_limit():
    """Should stop reading known keys once the budget is spent."""
    row = {"message": "x"}
    for index in range(logs.TAG_LIMIT + 5):
        row[f"tag{index}"] = index
    row["tags"] = {f"more{index}": index for index in range(30)}

    result = len(logs.to_event(row)["tags"])

    assert result <= logs.TAG_LIMIT


def test_an_otlp_exception_message_replaces_the_body():
    """Should prefer what the exception said over a generic log body."""
    document = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": "something happened",
                                "attributes": [
                                    {
                                        "key": "exception.message",
                                        "value": {"stringValue": "declined"},
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    result = logs.from_otlp(document)[0]["message"]
    expected = "declined"

    assert result == expected


def test_an_otlp_value_that_is_a_plain_string_is_read():
    """Should take the shorthand some exporters emit."""
    document = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"body": "plain"}]}]}]}

    result = logs.from_otlp(document)[0]["message"]
    expected = "plain"

    assert result == expected


def test_an_otlp_value_of_an_unknown_shape_is_stringified():
    """Should not lose an attribute because the protocol grew a new type."""
    document = {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "odd", "value": {"newKind": 7}}]},
                "scopeLogs": [{"logRecords": [{"body": "x"}]}],
            }
        ]
    }

    result = logs.from_otlp(document)[0]["odd"]

    assert "newKind" in result


def test_the_log_door_holds_the_size_cap(client, key, settings):
    """Should refuse an oversized batch — a door that skips the cap is a hole."""
    settings.PANDORA_INGEST_MAX_BYTES = 400

    response = client.post(
        f"/api/{key.project_id}/logs/?sentry_key={key.public_key}",
        data=json.dumps({"message": "x" * 3000}).encode(),
        content_type="application/x-ndjson",
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_the_otlp_door_holds_the_size_cap(client, key, settings):
    """Should hold the same cap on every entry point."""
    settings.PANDORA_INGEST_MAX_BYTES = 400

    response = client.post(
        f"/api/{key.project_id}/integration/otlp/v1/logs?sentry_key={key.public_key}",
        data=json.dumps({"padding": "x" * 3000}).encode(),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_a_module_qualified_exception_keeps_its_module():
    """Should carry `mypkg` separately, which is how two same-named errors stay apart."""
    trace = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 3, in charge\n'
        "    raise mypkg.PaymentError('declined')\n"
        "mypkg.PaymentError: declined"
    )

    event = logs.to_event({"message": "boom", "stack": trace})

    result = event["exception"]["values"][0]["module"]
    expected = "mypkg"

    assert result == expected


def test_a_record_with_no_body_becomes_an_empty_message():
    """Should not print `None` where the log line should be."""
    document = {
        "resourceLogs": [
            {"scopeLogs": [{"logRecords": [{"severityText": "error"}]}]},
        ]
    }

    result = logs.from_otlp(document)[0]["message"]
    expected = ""

    assert result == expected
