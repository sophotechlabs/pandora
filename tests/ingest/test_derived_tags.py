import datetime

import pytest
from django.utils import timezone

from pandora.events import payload as payload_interfaces
from pandora.ingest.translators import envelope
from pandora.ingest.translators import tags as derived_tags

RECEIVED_AT = datetime.datetime(2026, 9, 7, 12, 0, tzinfo=datetime.UTC)


def derive(raw, exception=None):
    return derived_tags.derive(payload_interfaces.normalize(raw), exception)


def test_a_user_becomes_a_tag_by_the_strongest_identity():
    """Should prefer the id over the other ways a person can be named."""
    result = derive(
        {
            "user": {
                "id": "42",
                "username": "arch",
                "email": "arch@example.test",
                "ip_address": "10.0.0.1",
            }
        }
    )

    assert result["user"] == "id:42"


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"username": "arch"}, "username:arch"),
        ({"email": "arch@example.test"}, "email:arch@example.test"),
        ({"ip_address": "10.0.0.1"}, "ip:10.0.0.1"),
    ],
)
def test_a_user_without_an_id_falls_back_in_order(user, expected):
    """Should name the person by whatever identity the payload carried."""
    assert derive({"user": user})["user"] == expected


def test_a_user_with_no_identity_produces_no_tag():
    """Should leave the tag out rather than write an empty one."""
    assert "user" not in derive({"user": {"segment": "beta"}})


def test_a_request_becomes_a_url_and_a_method_tag():
    """Should make the endpoint filterable without opening an event."""
    result = derive({"request": {"url": "https://app.test/checkout", "method": "post"}})

    assert result["url"] == "https://app.test/checkout"
    assert result["method"] == "POST"


def test_contexts_become_name_and_name_with_version_tags():
    """Should offer both the exact build and the family it belongs to."""
    result = derive(
        {
            "contexts": {
                "browser": {"name": "Chrome", "version": "141.0"},
                "os": {"name": "Linux", "version": "6.12"},
                "runtime": {"name": "CPython", "version": "3.14.7"},
                "device": {"model": "Pixel 9"},
            }
        }
    )

    assert result["browser"] == "Chrome 141.0"
    assert result["browser.name"] == "Chrome"
    assert result["os"] == "Linux 6.12"
    assert result["runtime"] == "CPython 3.14.7"
    assert result["device"] == "Pixel 9"


def test_a_context_without_a_version_tags_the_name_alone():
    """Should not append a trailing space when no version came in."""
    result = derive({"contexts": {"browser": {"name": "Firefox"}}})

    assert result["browser"] == "Firefox"


def test_a_trace_context_becomes_a_trace_tag():
    """Should let one trace id gather every error it produced."""
    result = derive(
        {"contexts": {"trace": {"trace_id": "c" * 32, "span_id": "d" * 16}}}
    )

    assert result["trace"] == "c" * 32


def test_a_mechanism_becomes_a_mechanism_and_handled_tag():
    """Should record how the exception was caught, which triage reads first."""
    result = derive(
        {},
        {"type": "ValueError", "mechanism": {"type": "django", "handled": False}},
    )

    assert result["mechanism"] == "django"
    assert result["handled"] == "no"


def test_a_handled_exception_says_so():
    """Should distinguish a caught exception from one that killed the request."""
    result = derive({}, {"mechanism": {"handled": True}})

    assert result["handled"] == "yes"


def test_a_mechanism_without_a_handled_flag_leaves_the_tag_out():
    """Should not guess at a flag the SDK never sent."""
    assert "handled" not in derive({}, {"mechanism": {"type": "generic"}})


def test_a_missing_interface_produces_no_tags():
    """Should stay silent rather than invent keys for an empty payload."""
    assert derive({}) == {}


def test_a_malformed_interface_produces_no_tags():
    """Should ignore a payload whose interfaces are the wrong shape."""
    assert derive({"user": "arch", "request": 3, "contexts": []}) == {}


def test_a_long_value_is_cut_to_the_tag_limit():
    """Should keep one huge URL from becoming a huge tag row."""
    result = derive({"request": {"url": "https://app.test/" + "a" * 500}})

    assert len(result["url"]) == derived_tags.VALUE_MAX


def test_a_logger_becomes_a_tag():
    """Should let a noisy logger be filtered out of the stream."""
    assert derive({"logger": "django.request"})["logger"] == "django.request"


def test_translation_carries_the_derived_tags_onto_the_occurrence(project):
    """Should reach the occurrence, which is what the aggregation counts."""
    occurrence = envelope.translate_event(
        {
            "event_id": "b" * 32,
            "logger": "django.request",
            "user": {"id": "42"},
            "request": {"url": "https://app.test/checkout", "method": "GET"},
            "contexts": {"browser": {"name": "Chrome", "version": "141.0"}},
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "value": "bad input",
                        "mechanism": {"type": "django", "handled": False},
                    }
                ]
            },
        },
        project,
        received_at=RECEIVED_AT,
    )

    assert occurrence.tags["user"] == "id:42"
    assert occurrence.tags["url"] == "https://app.test/checkout"
    assert occurrence.tags["browser"] == "Chrome 141.0"
    assert occurrence.tags["handled"] == "no"
    assert occurrence.tags["logger"] == "django.request"


def test_a_declared_tag_wins_over_a_derived_one(project):
    """Should never overwrite what the caller said with what was inferred."""
    occurrence = envelope.translate_event(
        {
            "event_id": "b" * 32,
            "message": "boom",
            "logger": "django.request",
            "tags": {"logger": "mine"},
        },
        project,
        received_at=RECEIVED_AT,
    )

    assert occurrence.tags["logger"] == "mine"


def test_a_derived_user_tag_is_scrubbed_like_any_other(project, settings):
    """Should redact an address the same way the rest of the payload is."""
    settings.PANDORA_SCRUB_ANONYMISE_IP = True
    occurrence = envelope.translate_event(
        {
            "event_id": "b" * 32,
            "message": "boom",
            "user": {"ip_address": "203.0.113.7"},
        },
        project,
        received_at=timezone.now(),
    )

    assert "203.0.113.7" not in occurrence.tags["user"]
