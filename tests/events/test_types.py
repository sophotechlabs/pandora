import dataclasses
import datetime

from pandora.events import types

# field contract


def test_event_carries_the_frozen_field_set():
    """Should expose exactly the columns the vendor-branched DDL creates."""
    result = [field.name for field in dataclasses.fields(types.Event)]
    expected = [
        "id",
        "project_id",
        "timestamp",
        "level",
        "message",
        "issue_id",
        "episode_id",
        "fingerprint",
        "tags",
        "extra",
        "source",
        "environment",
    ]

    assert result == expected


def test_event_is_immutable():
    """Should refuse mutation — payload blobs are append-only."""
    event = types.Event(
        id=types.new_event_id(),
        project_id=1,
        timestamp=datetime.datetime(2026, 8, 4, 9, 12, tzinfo=datetime.UTC),
        level="error",
        message="Pod is crash looping.",
    )

    assert dataclasses.is_dataclass(event) is True
    assert event.__dataclass_params__.frozen is True


def test_event_defaults_to_an_unlinked_alertmanager_occurrence():
    """Should default the optional fields to empty, source am, no issue link."""
    event = types.Event(
        id=types.new_event_id(),
        project_id=1,
        timestamp=datetime.datetime(2026, 8, 4, 9, 12, tzinfo=datetime.UTC),
        level="error",
        message="Pod is crash looping.",
    )

    result = {
        "issue_id": event.issue_id,
        "episode_id": event.episode_id,
        "fingerprint": event.fingerprint,
        "tags": event.tags,
        "extra": event.extra,
        "source": event.source,
        "environment": event.environment,
    }
    expected = {
        "issue_id": None,
        "episode_id": None,
        "fingerprint": [],
        "tags": {},
        "extra": {},
        "source": "am",
        "environment": "",
    }
    assert result == expected


def test_the_table_name_is_shared_with_the_stores():
    """Should pin one table name the migration and both stores agree on."""
    result = types.EVENTS_TABLE
    expected = "events_event"

    assert result == expected


# id generation tests


def test_new_event_id_is_a_ulid():
    """Should mint a 26-character Crockford base32 ULID."""
    result = types.new_event_id()

    assert len(result) == 26
    assert result.isalnum() is True


def test_new_event_ids_are_distinct():
    """Should not repeat within a burst."""
    result = {types.new_event_id() for _ in range(50)}

    assert len(result) == 50


def test_new_event_ids_sort_in_creation_order():
    """Should sort lexicographically by time so `before` cursors work."""
    result = [types.new_event_id() for _ in range(20)]
    expected = sorted(result)

    assert result == expected
