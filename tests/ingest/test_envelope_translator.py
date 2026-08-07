import datetime
import json

import pytest
from django.utils import timezone

from pandora.ingest.translators import envelope
from pandora.issues import models as issue_models

RECEIVED_AT = datetime.datetime(2026, 8, 4, 18, 30, tzinfo=datetime.UTC)


def line(payload):
    return json.dumps(payload).encode()


def body(*parts):
    return b"\n".join(parts)


def event_payload(**overrides):
    payload = {
        "event_id": "b" * 32,
        "level": "error",
        "platform": "python",
        "timestamp": "2026-08-04T18:29:00Z",
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "module": "pandora.ingest",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "django.core.handlers",
                                "function": "inner",
                                "in_app": False,
                            },
                            {
                                "module": "pandora.ingest.views",
                                "function": "envelope",
                                "in_app": True,
                            },
                        ]
                    },
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


# envelope parsing


def test_an_envelope_splits_into_its_header_and_items():
    """Should read the newline-delimited header then each item header and payload."""
    raw = body(
        line({"event_id": "a" * 32}),
        line({"type": "event"}),
        line({"message": "hello"}),
    )

    parsed = envelope.parse_envelope(raw)

    result = (parsed.event_id, [item.type for item in parsed.items])
    expected = ("a" * 32, ["event"])
    assert result == expected


def test_a_declared_length_reads_exactly_that_many_bytes():
    """Should honour the item length header instead of hunting for a newline."""
    payload = b'{"message":"one\\ntwo"}'
    raw = body(
        line({"event_id": "a" * 32}),
        line({"type": "event", "length": len(payload)}),
        payload,
        line({"type": "attachment"}),
        b"trailing",
    )

    parsed = envelope.parse_envelope(raw)

    result = [(item.type, item.payload) for item in parsed.items]
    expected = [("event", payload), ("attachment", b"trailing")]
    assert result == expected


def test_a_payload_shorter_than_its_declared_length_is_refused():
    """Should refuse a truncated item rather than store half an event."""
    raw = body(
        line({"event_id": "a" * 32}),
        line({"type": "event", "length": 500}),
        b'{"message":"short"}',
    )

    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(raw)


def test_several_items_are_kept_in_order():
    """Should keep every item, event or not, in the order the SDK sent them."""
    raw = body(
        line({"event_id": "a" * 32}),
        line({"type": "session"}),
        line({"status": "ok"}),
        line({"type": "event"}),
        line({"message": "hello"}),
        line({"type": "transaction"}),
        line({"spans": []}),
    )

    parsed = envelope.parse_envelope(raw)

    result = [item.type for item in parsed.items]
    expected = ["session", "event", "transaction"]
    assert result == expected


def test_only_event_items_are_selected_for_processing():
    """Should hand the consumer event items alone, leaving the rest acked."""
    raw = body(
        line({"event_id": "a" * 32}),
        line({"type": "session"}),
        line({"status": "ok"}),
        line({"type": "event"}),
        line({"message": "hello"}),
    )

    parsed = envelope.parse_envelope(raw)

    result = len(envelope.event_items(parsed))
    expected = 1
    assert result == expected


@pytest.mark.parametrize("raw", [b"", b"   ", b"\n"])
def test_an_empty_envelope_is_refused(raw):
    """Should refuse an empty body rather than treat it as a valid envelope."""
    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(raw)


def test_a_header_that_is_not_json_is_refused():
    """Should refuse an envelope whose header line is not JSON."""
    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(b"not json\n")


def test_a_header_that_is_not_an_object_is_refused():
    """Should refuse an envelope header that parses to a list."""
    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(b"[1, 2]\n")


def test_an_item_header_that_is_not_json_is_refused():
    """Should refuse an item whose header line is not JSON."""
    raw = body(line({"event_id": "a" * 32}), b"not json", b"{}")

    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(raw)


def test_an_item_header_that_is_not_an_object_is_refused():
    """Should refuse an item header that parses to a list."""
    raw = body(line({"event_id": "a" * 32}), b"[1]", b"{}")

    with pytest.raises(envelope.EnvelopeError):
        envelope.parse_envelope(raw)


def test_an_envelope_header_alone_carries_no_items():
    """Should accept a header-only envelope, which is what a ping looks like."""
    parsed = envelope.parse_envelope(line({"event_id": "a" * 32}))

    result = parsed.items
    expected = []
    assert result == expected


def test_an_envelope_without_an_event_id_reports_an_empty_one():
    """Should not invent an id when the header omits it."""
    parsed = envelope.parse_envelope(line({"sent_at": "2026-08-04T18:30:00Z"}))

    result = parsed.event_id
    expected = ""
    assert result == expected


# event id


def test_the_stored_event_id_is_a_ulid_derived_from_the_sentry_id():
    """Should stay time-sortable, because the store orders by id."""
    stamp = datetime.datetime(2026, 8, 4, 18, 29, tzinfo=datetime.UTC)

    result = envelope.event_id(1, "b" * 32, stamp)
    expected = envelope.event_id(1, "b" * 32, stamp)
    assert result == expected
    assert len(result) == 26


def test_two_projects_never_share_a_stored_event_id():
    """Should keep the same SDK event id apart per project."""
    stamp = datetime.datetime(2026, 8, 4, 18, 29, tzinfo=datetime.UTC)

    result = envelope.event_id(1, "b" * 32, stamp)
    other = envelope.event_id(2, "b" * 32, stamp)
    assert result != other


def test_a_prehistoric_timestamp_still_yields_an_id():
    """Should clamp a negative epoch rather than raise while building the id."""
    stamp = datetime.datetime(1960, 1, 1, tzinfo=datetime.UTC)

    result = len(envelope.event_id(1, "b" * 32, stamp))
    expected = 26
    assert result == expected


# translation


@pytest.mark.django_db
def test_an_exception_event_takes_its_title_from_type_and_culprit(project):
    """Should name the group, not one victim — the value varies per event."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = occurrence.title
    expected = "ValueError: pandora.ingest.views in envelope"
    assert result == expected


@pytest.mark.django_db
def test_the_per_event_value_survives_on_the_message(project):
    """Should keep the varying detail where the event list can still show it."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = occurrence.message
    expected = "ValueError: bad input"
    assert result == expected


@pytest.mark.django_db
def test_two_events_of_one_group_share_a_title(project):
    """Should title both the same — the old title froze on the first URL seen."""
    other = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "a different url entirely",
                    "module": "pandora.ingest",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "pandora.ingest.views",
                                "function": "envelope",
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        }
    )

    result = envelope.translate_event(other, project, received_at=RECEIVED_AT).title
    expected = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    ).title
    assert result == expected


@pytest.mark.django_db
def test_an_exception_without_a_stack_titles_on_the_type_alone(project):
    """Should not append an empty culprit to the title."""
    payload = event_payload(
        exception={"values": [{"type": "ValueError", "module": "pandora"}]}
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.title
    expected = "ValueError"
    assert result == expected


@pytest.mark.django_db
def test_a_log_event_titles_on_its_template(project):
    """Should title on the template — the formatted line names one source."""
    payload = {
        "event_id": "c" * 32,
        "logentry": {"formatted": "disk almost full", "message": "disk %s full"},
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = (occurrence.title, occurrence.message)
    expected = ("disk %s full", "disk almost full")
    assert result == expected


@pytest.mark.django_db
def test_a_logentry_without_a_formatted_line_uses_its_raw_message(project):
    """Should accept the unformatted logentry when that is all there is."""
    payload = {"event_id": "c" * 32, "logentry": {"message": "raw message"}}

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.title
    expected = "raw message"
    assert result == expected


@pytest.mark.django_db
def test_a_bare_message_event_titles_on_the_message(project):
    """Should use the top-level message when neither exception nor logentry exists."""
    payload = {"event_id": "c" * 32, "message": "something happened"}

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.title
    expected = "something happened"
    assert result == expected


@pytest.mark.django_db
def test_an_event_with_nothing_to_say_still_gets_a_title(project):
    """Should never store an empty title."""
    occurrence = envelope.translate_event(
        {"event_id": "c" * 32}, project, received_at=RECEIVED_AT
    )

    result = (occurrence.title, occurrence.fingerprint)
    expected = ("Unknown event", ["Unknown event"])
    assert result == expected


@pytest.mark.django_db
def test_the_culprit_comes_from_the_top_in_app_frame(project):
    """Should point at the app's own frame, not the framework's."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = occurrence.culprit
    expected = "pandora.ingest.views in envelope"
    assert result == expected


@pytest.mark.django_db
def test_a_stack_with_no_in_app_frame_falls_back_to_the_last_one(project):
    """Should still name a culprit when the SDK marks nothing in-app."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"module": "a", "function": "one"},
                            {"module": "b", "function": "two"},
                        ]
                    },
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = "b in two"
    assert result == expected


@pytest.mark.django_db
def test_a_frame_without_a_module_names_the_function(project):
    """Should degrade to the function name alone."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {"frames": [{"function": "handler", "in_app": True}]},
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = "handler"
    assert result == expected


@pytest.mark.django_db
def test_a_frame_with_only_a_file_names_the_line(project):
    """Should degrade to filename:lineno when there is no function."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [{"filename": "app.py", "lineno": 42, "in_app": True}]
                    },
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = "app.py:42"
    assert result == expected


@pytest.mark.django_db
def test_an_event_without_a_stack_has_no_culprit(project):
    """Should leave the culprit empty rather than guess."""
    payload = event_payload(exception={"values": [{"type": "ValueError"}]})

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = ""
    assert result == expected


@pytest.mark.django_db
def test_the_default_fingerprint_is_the_exception_and_the_culprit_frame(project):
    """Should group by where it was raised, not by the exception class alone."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = occurrence.fingerprint
    expected = ["pandora.ingest", "ValueError", "pandora.ingest.views", "envelope"]
    assert result == expected


@pytest.mark.django_db
def test_one_exception_class_raised_in_two_places_is_two_issues(project):
    """Should split by call site — every HTTPError used to land in one issue."""
    elsewhere = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "module": "pandora.ingest",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "pandora.ingest.queue",
                                "function": "publish",
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        }
    )

    result = envelope.translate_event(
        elsewhere, project, received_at=RECEIVED_AT
    ).fingerprint_hash
    other = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    ).fingerprint_hash
    assert result != other


@pytest.mark.django_db
def test_the_fingerprint_ignores_the_line_the_frame_sits_on(project):
    """Should survive a deploy that shifted the file — same bug, new lineno."""
    moved = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "module": "pandora.ingest",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "pandora.ingest.views",
                                "function": "envelope",
                                "lineno": 412,
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        }
    )

    result = envelope.translate_event(moved, project, received_at=RECEIVED_AT)
    expected = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )
    assert result.fingerprint == expected.fingerprint


@pytest.mark.django_db
def test_the_fingerprint_ignores_the_exception_value(project):
    """Should not split on the value — one URL per source is one issue per source."""
    other = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "https://example.test/board/9782",
                    "module": "pandora.ingest",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "pandora.ingest.views",
                                "function": "envelope",
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        }
    )

    result = envelope.translate_event(other, project, received_at=RECEIVED_AT)
    expected = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )
    assert result.fingerprint == expected.fingerprint


@pytest.mark.django_db
def test_a_frame_with_no_module_fingerprints_on_its_function(project):
    """Should still separate call sites when the SDK sends no frame module."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {"frames": [{"function": "handler", "in_app": True}]},
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["ValueError", "handler"]
    assert result == expected


@pytest.mark.django_db
def test_a_frame_with_only_a_filename_fingerprints_on_the_file(project):
    """Should fall back to the file, still without the line it moved to."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [{"filename": "app.py", "lineno": 42, "in_app": True}]
                    },
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["ValueError", "app.py"]
    assert result == expected


@pytest.mark.django_db
def test_an_exception_without_a_stack_fingerprints_on_the_class_alone(project):
    """Should group on what it can when the SDK sends no frames."""
    payload = event_payload(
        exception={"values": [{"type": "ValueError", "module": "pandora.ingest"}]}
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["pandora.ingest", "ValueError"]
    assert result == expected


@pytest.mark.django_db
def test_an_explicit_fingerprint_is_honoured(project):
    """Should let the SDK decide grouping when it says so."""
    payload = event_payload(fingerprint=["checkout", "timeout"])

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["checkout", "timeout"]
    assert result == expected


@pytest.mark.django_db
def test_the_default_placeholder_expands_inside_an_explicit_fingerprint(project):
    """Should splice the derived parts in where the SDK wrote the placeholder."""
    payload = event_payload(fingerprint=["{{ default }}", "tenant-7"])

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = [
        "pandora.ingest",
        "ValueError",
        "pandora.ingest.views",
        "envelope",
        "tenant-7",
    ]
    assert result == expected


@pytest.mark.django_db
def test_an_empty_fingerprint_list_falls_back_to_the_default(project):
    """Should not produce an unhashable empty fingerprint."""
    payload = event_payload(fingerprint=[])

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["pandora.ingest", "ValueError", "pandora.ingest.views", "envelope"]
    assert result == expected


@pytest.mark.django_db
def test_a_message_event_fingerprints_on_its_logentry(project):
    """Should group log events by the only line they carry."""
    payload = {"event_id": "c" * 32, "logentry": {"formatted": "disk almost full"}}

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["disk almost full"]
    assert result == expected


@pytest.mark.django_db
def test_a_log_event_fingerprints_on_the_template_not_the_formatted_line(project):
    """Should keep one issue per log call — formatted gives one per source."""
    payload = {
        "event_id": "c" * 32,
        "logger": "listopad.core.tasks",
        "logentry": {
            "message": "fetch failed for source %s",
            "params": ["corepilot-greenhouse"],
            "formatted": "fetch failed for source corepilot-greenhouse",
        },
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["listopad.core.tasks", "fetch failed for source %s"]
    assert result == expected


@pytest.mark.django_db
def test_two_sources_of_one_log_call_share_an_issue(project):
    """Should collapse the per-source explosion the formatted line created."""
    template = "fetch failed for source %s"
    first = {
        "event_id": "c" * 32,
        "logger": "listopad.core.tasks",
        "logentry": {"message": template, "formatted": "fetch failed for source a"},
    }
    second = {
        "event_id": "d" * 32,
        "logger": "listopad.core.tasks",
        "logentry": {"message": template, "formatted": "fetch failed for source b"},
    }

    result = envelope.translate_event(first, project, received_at=RECEIVED_AT)
    other = envelope.translate_event(second, project, received_at=RECEIVED_AT)
    assert result.fingerprint_hash == other.fingerprint_hash


@pytest.mark.django_db
def test_two_loggers_sharing_a_template_stay_apart(project):
    """Should keep the logger in the identity — two modules are two issues."""
    template = {"message": "fetch failed for source %s"}
    first = {
        "event_id": "c" * 32,
        "logger": "listopad.core.tasks",
        "logentry": template,
    }
    second = {
        "event_id": "d" * 32,
        "logger": "listopad.jobs.tech",
        "logentry": template,
    }

    result = envelope.translate_event(first, project, received_at=RECEIVED_AT)
    other = envelope.translate_event(second, project, received_at=RECEIVED_AT)
    assert result.fingerprint_hash != other.fingerprint_hash


@pytest.mark.django_db
def test_a_bare_message_event_fingerprints_on_the_message(project):
    """Should group on the message when there is nothing else."""
    payload = {"event_id": "c" * 32, "message": "boom"}

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["boom"]
    assert result == expected


@pytest.mark.django_db
def test_an_exception_without_a_type_falls_through_to_the_message(project):
    """Should not fingerprint on an empty exception type."""
    payload = {
        "event_id": "c" * 32,
        "message": "boom",
        "exception": {"values": [{"value": "no type here"}]},
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.fingerprint
    expected = ["boom"]
    assert result == expected


@pytest.mark.django_db
def test_an_exception_list_is_read_like_a_values_mapping(project):
    """Should accept the older shape where exception is a bare list."""
    payload = {
        "event_id": "c" * 32,
        "exception": [{"type": "KeyError", "value": "missing", "module": "app"}],
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.message
    expected = "KeyError: missing"
    assert result == expected


@pytest.mark.django_db
def test_the_last_exception_in_a_chain_is_the_one_reported(project):
    """Should report the raised exception, which Sentry sends last."""
    payload = {
        "event_id": "c" * 32,
        "exception": {
            "values": [
                {"type": "ValueError", "value": "cause"},
                {"type": "RuntimeError", "value": "effect"},
            ]
        },
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.message
    expected = "RuntimeError: effect"
    assert result == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("fatal", issue_models.Level.FATAL),
        ("critical", issue_models.Level.FATAL),
        ("error", issue_models.Level.ERROR),
        ("warning", issue_models.Level.WARNING),
        ("info", issue_models.Level.INFO),
        ("debug", issue_models.Level.DEBUG),
        ("nonsense", issue_models.Level.ERROR),
        ("", issue_models.Level.ERROR),
    ],
)
def test_sentry_levels_map_onto_the_schema(project, level, expected):
    """Should translate every level the SDKs send into one the schema accepts."""
    payload = event_payload(level=level)

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.level
    assert result == expected


@pytest.mark.django_db
def test_the_payload_environment_wins_over_the_door(project):
    """Should let the SDK name its environment, unlike the Alertmanager door."""
    payload = event_payload(environment="staging")

    occurrence = envelope.translate_event(
        payload, project, environment="p-mk1", received_at=RECEIVED_AT
    )

    result = occurrence.environment
    expected = "staging"
    assert result == expected


@pytest.mark.django_db
def test_the_door_environment_is_used_when_the_payload_is_silent(project):
    """Should keep an environment rather than store none."""
    occurrence = envelope.translate_event(
        event_payload(), project, environment="p-mk1", received_at=RECEIVED_AT
    )

    result = occurrence.environment
    expected = "p-mk1"
    assert result == expected


@pytest.mark.django_db
def test_tags_are_flattened_to_strings(project):
    """Should store tag values as strings, whatever the SDK sent."""
    payload = event_payload(tags={"release": "1.2.3", "retries": 4})

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.tags
    expected = {"release": "1.2.3", "retries": "4"}
    assert result == expected


@pytest.mark.django_db
def test_tags_sent_as_pairs_are_accepted(project):
    """Should accept the list-of-pairs shape older SDKs send."""
    payload = event_payload(tags=[["release", "1.2.3"], ["bad"], "nope"])

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.tags
    expected = {"release": "1.2.3"}
    assert result == expected


@pytest.mark.django_db
def test_a_long_tag_value_is_capped(project):
    """Should not let one tag value blow up the aggregate tables."""
    payload = event_payload(tags={"path": "x" * 500})

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = len(occurrence.tags["path"])
    expected = 200
    assert result == expected


@pytest.mark.django_db
def test_release_and_transaction_become_tags(project):
    """Should make the fields people filter by available as tags."""
    payload = event_payload(release="1.4.0", transaction="POST /ingest")

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = (occurrence.tags["release"], occurrence.tags["transaction"])
    expected = ("1.4.0", "POST /ingest")
    assert result == expected


@pytest.mark.django_db
def test_an_explicit_tag_is_not_overwritten_by_the_field(project):
    """Should keep the SDK's own tag when both are present."""
    payload = event_payload(release="1.4.0", tags={"release": "explicit"})

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.tags["release"]
    expected = "explicit"
    assert result == expected


@pytest.mark.django_db
def test_context_is_kept_in_extra(project):
    """Should retain the payload's context for the detail page."""
    payload = event_payload(
        extra={"order_id": 7}, contexts={"runtime": {"name": "cpy"}}
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = (occurrence.extra["extra"], occurrence.extra["platform"])
    expected = ({"order_id": 7}, "python")
    assert result == expected


@pytest.mark.django_db
def test_empty_context_fields_are_left_out_of_extra(project):
    """Should not store empty keys just because the SDK sent them."""
    payload = event_payload(request={}, modules=[], sdk=None)

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = sorted(occurrence.extra)
    expected = ["platform"]
    assert result == expected


@pytest.mark.django_db
def test_an_iso_timestamp_is_the_occurrence_start(project):
    """Should trust the SDK's own clock for when the event happened."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = occurrence.starts_at
    expected = datetime.datetime(2026, 8, 4, 18, 29, tzinfo=datetime.UTC)
    assert result == expected


@pytest.mark.django_db
def test_an_epoch_timestamp_is_accepted(project):
    """Should accept the float seconds form the SDKs also send."""
    payload = event_payload(timestamp=1785868140.0)

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.starts_at
    expected = datetime.datetime.fromtimestamp(1785868140.0, tz=datetime.UTC)
    assert result == expected


@pytest.mark.django_db
def test_a_naive_timestamp_is_read_as_utc(project):
    """Should not let a missing offset shift an event by hours."""
    payload = event_payload(timestamp="2026-08-04T18:29:00")

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.starts_at
    expected = datetime.datetime(2026, 8, 4, 18, 29, tzinfo=datetime.UTC)
    assert result == expected


@pytest.mark.django_db
@pytest.mark.parametrize("stamp", ["", "  ", "not-a-date", 7j, None])
def test_an_unusable_timestamp_falls_back_to_arrival(project, stamp):
    """Should date the event on arrival rather than refuse it."""
    payload = event_payload(timestamp=stamp)

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.starts_at
    expected = RECEIVED_AT
    assert result == expected


@pytest.mark.django_db
def test_an_sdk_occurrence_carries_no_episode_identity(project):
    """Should leave the Alertmanager-only fields empty on the SDK door."""
    occurrence = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    )

    result = (
        occurrence.am_fingerprint,
        occurrence.labels,
        occurrence.grouping_labels,
        occurrence.ends_at,
        occurrence.source,
    )
    expected = ("", {}, {}, None, "sdk")
    assert result == expected


@pytest.mark.django_db
def test_an_event_item_that_is_not_an_object_is_refused(project):
    """Should refuse a payload that is not a JSON object."""
    with pytest.raises(envelope.EnvelopeError):
        envelope.translate_event([1, 2], project, received_at=RECEIVED_AT)


def test_the_sentry_event_id_falls_back_to_the_envelope_header():
    """Should use the header's id when the item omits its own."""
    result = envelope.sentry_event_id({}, "a" * 32)
    expected = "a" * 32
    assert result == expected


def test_the_item_event_id_wins_over_the_header():
    """Should prefer the id on the event itself."""
    result = envelope.sentry_event_id({"event_id": "b" * 32}, "a" * 32)
    expected = "b" * 32
    assert result == expected


@pytest.mark.django_db
def test_translation_never_touches_the_clock_when_given_a_time(project):
    """Should be deterministic — the arrival time is passed in, never read."""
    first = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    ).timestamp
    second = envelope.translate_event(
        event_payload(), project, received_at=RECEIVED_AT
    ).timestamp

    result = (first, second, first == timezone.now())
    expected = (RECEIVED_AT, RECEIVED_AT, False)
    assert result == expected


@pytest.mark.django_db
def test_an_exception_list_with_no_object_falls_through(project):
    """Should ignore junk in the values list rather than raise."""
    payload = {
        "event_id": "c" * 32,
        "message": "boom",
        "exception": {"values": ["not an object"]},
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.title
    expected = "boom"
    assert result == expected


@pytest.mark.django_db
def test_an_exception_whose_values_are_not_a_list_falls_through(project):
    """Should ignore an exception object shaped in a way no SDK sends."""
    payload = {
        "event_id": "c" * 32,
        "message": "boom",
        "exception": {"values": "nope"},
    }

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.title
    expected = "boom"
    assert result == expected


@pytest.mark.django_db
def test_a_frame_with_only_a_filename_names_the_file(project):
    """Should name the file when there is no function and no usable line."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [{"filename": "app.py", "in_app": True}],
                    },
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = "app.py"
    assert result == expected


@pytest.mark.django_db
def test_a_stacktrace_whose_frames_are_not_a_list_has_no_culprit(project):
    """Should not guess a culprit from a malformed stacktrace."""
    payload = event_payload(
        exception={
            "values": [
                {"type": "ValueError", "value": "bad", "stacktrace": {"frames": "nope"}}
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = ""
    assert result == expected


@pytest.mark.django_db
def test_junk_between_frames_is_skipped(project):
    """Should walk past a non-object frame to the real one."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"module": "app", "function": "handler", "in_app": True},
                            "not a frame",
                        ]
                    },
                }
            ]
        }
    )

    occurrence = envelope.translate_event(payload, project, received_at=RECEIVED_AT)

    result = occurrence.culprit
    expected = "app in handler"
    assert result == expected


def test_a_null_event_id_is_treated_as_absent():
    """Should not read the string None as an id — that collapsed every id-less event."""
    result = envelope.sentry_event_id({"event_id": None}, "fallback-id")
    expected = "fallback-id"
    assert result == expected


def test_a_blank_event_id_is_treated_as_absent():
    """Should fall back rather than claim the empty string as a dedup key."""
    result = envelope.sentry_event_id({"event_id": "   "}, "fallback-id")
    expected = "fallback-id"
    assert result == expected
