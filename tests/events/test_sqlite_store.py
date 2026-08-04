import datetime
import json

import pytest

from pandora.events import sqlite_store
from tests.events import support

pytestmark = pytest.mark.django_db(databases="__all__")


def raw_row(store, event_id):
    with store.connection.cursor() as cursor:
        cursor.execute(
            'SELECT "timestamp", fingerprint, tags, extra FROM events_event '
            "WHERE id = %s",
            [event_id],
        )
        columns = [column[0] for column in cursor.description]
        return dict(zip(columns, cursor.fetchone(), strict=True))


# statement contract


def test_every_statement_targets_the_migrated_table():
    """Should build all three statements against the one events table."""
    result = [
        "events_event" in sqlite_store.INSERT,
        "events_event" in sqlite_store.SELECT,
        "events_event" in sqlite_store.DELETE,
    ]
    expected = [True, True, True]

    assert result == expected


def test_insert_ignores_a_conflicting_row_rather_than_failing():
    """Should use INSERT OR IGNORE so a replay is a no-op, not an error."""
    result = sqlite_store.INSERT.startswith("INSERT OR IGNORE INTO")

    assert result is True


# timestamp encoding


def test_a_timestamp_is_stored_as_fixed_width_utc_text():
    """Should encode to microsecond ISO text so string ordering is time ordering."""
    stamp = datetime.datetime(2026, 8, 4, 9, 12, 41, 7, tzinfo=datetime.UTC)

    result = sqlite_store._encode_timestamp(stamp)
    expected = "2026-08-04T09:12:41.000007+00:00"

    assert result == expected


def test_a_non_utc_timestamp_is_normalised_before_storage():
    """Should convert to UTC so every stored string sorts on one clock."""
    kyiv = datetime.timezone(datetime.timedelta(hours=3))
    stamp = datetime.datetime(2026, 8, 4, 12, 12, 41, tzinfo=kyiv)

    result = sqlite_store._encode_timestamp(stamp)
    expected = "2026-08-04T09:12:41.000000+00:00"

    assert result == expected


def test_a_naive_timestamp_is_read_as_utc():
    """Should assume UTC rather than the process timezone for a naive value."""
    stamp = datetime.datetime(2026, 8, 4, 9, 12, 41)

    result = sqlite_store._encode_timestamp(stamp)
    expected = "2026-08-04T09:12:41.000000+00:00"

    assert result == expected


def test_encoded_timestamps_sort_in_time_order():
    """Should keep lexicographic order equal to chronological order."""
    base = datetime.datetime(2026, 8, 4, 9, 12, 41, tzinfo=datetime.UTC)
    stamps = [
        base,
        base + datetime.timedelta(microseconds=1),
        base + datetime.timedelta(seconds=1),
        base + datetime.timedelta(days=40),
    ]

    result = [sqlite_store._encode_timestamp(stamp) for stamp in stamps]
    expected = sorted(result)

    assert result == expected


def test_an_encoded_timestamp_decodes_back_to_the_same_instant():
    """Should round-trip without losing microseconds or the offset."""
    stamp = datetime.datetime(2026, 8, 4, 9, 12, 41, 654321, tzinfo=datetime.UTC)

    result = sqlite_store._decode_timestamp(sqlite_store._encode_timestamp(stamp))
    expected = stamp

    assert result == expected


# storage shape


def test_json_fields_are_stored_as_text(sqlite_event_store, moment):
    """Should serialise the JSON columns itself — SQLite has no jsonb type."""
    event = support.make_event(0, moment)
    sqlite_event_store.insert([event])

    row = raw_row(sqlite_event_store, event.id)

    result = [
        json.loads(row["fingerprint"]),
        json.loads(row["tags"]),
        json.loads(row["extra"]),
    ]
    expected = [event.fingerprint, event.tags, event.extra]
    assert result == expected


def test_the_timestamp_column_holds_the_encoded_text(sqlite_event_store, moment):
    """Should write the timestamp through the store's own encoder."""
    event = support.make_event(0, moment)
    sqlite_event_store.insert([event])

    row = raw_row(sqlite_event_store, event.id)

    result = row["timestamp"]
    expected = sqlite_store._encode_timestamp(event.timestamp)
    assert result == expected


# prune granularity


def test_prune_is_row_granular(sqlite_event_store, moment):
    """Should delete a row inside the cutoff's own month — no partition to keep."""
    older = support.make_event(0, moment, timestamp=moment - datetime.timedelta(days=1))
    newer = support.make_event(1, moment)
    sqlite_event_store.insert([older, newer])

    removed = sqlite_event_store.prune(moment)

    result = [removed, support.ids(sqlite_event_store.fetch(1))]
    expected = [1, [support.event_id(1)]]
    assert result == expected


def test_prune_treats_the_cutoff_as_exclusive(sqlite_event_store, moment):
    """Should keep an event stamped exactly at the cutoff."""
    event = support.make_event(0, moment)
    sqlite_event_store.insert([event])

    removed = sqlite_event_store.prune(moment)

    result = [removed, support.ids(sqlite_event_store.fetch(1))]
    expected = [0, [support.event_id(0)]]
    assert result == expected


# identity


def test_the_event_id_alone_is_the_primary_key(sqlite_event_store, moment):
    """Should reject a second row with the same id under a different timestamp."""
    first = support.make_event(0, moment)
    second = support.make_event(
        0, moment, timestamp=moment + datetime.timedelta(hours=2)
    )
    sqlite_event_store.insert([first])

    sqlite_event_store.insert([second])

    result = len(sqlite_event_store.fetch(1))
    expected = 1
    assert result == expected


# partitions


def test_ensure_partitions_stays_a_no_op(sqlite_event_store, moment):
    """Should do nothing at any horizon — SQLite holds one plain table."""
    sqlite_event_store.insert([support.make_event(0, moment)])

    result = sqlite_event_store.ensure_partitions(months_ahead=12)

    assert result is None
    assert len(sqlite_event_store.fetch(1)) == 1
