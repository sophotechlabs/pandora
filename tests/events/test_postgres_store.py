import datetime

import pytest
from django import db, test
from django.db import transaction
from django.utils import timezone

from pandora.events import postgres_store
from tests.events import support

pytestmark = pytest.mark.django_db(databases="__all__")

FIRING = "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')"


def partitions(store):
    with store.connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_inherits i ON i.inhrelid = c.oid "
            "WHERE i.inhparent = 'events_event'::regclass ORDER BY c.relname"
        )
        return [name for (name,) in cursor.fetchall()]


def partition_name(day):
    return f"events_event_{day.year}_{day.month:02d}"


def month_after(day, count):
    for _ in range(count):
        day = (day.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)
    return day


def month_before(day, count):
    for _ in range(count):
        day = (day.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    return day


# statement contract


def test_every_statement_targets_the_migrated_table():
    """Should build both statements against the one events table."""
    result = [
        "events_event" in postgres_store.INSERT,
        "events_event" in postgres_store.SELECT,
    ]
    expected = [True, True]

    assert result == expected


def test_insert_ignores_a_conflicting_row_rather_than_failing():
    """Should end in ON CONFLICT DO NOTHING so a replay is a no-op."""
    result = postgres_store.INSERT.endswith("ON CONFLICT DO NOTHING")

    assert result is True


def test_the_four_json_columns_are_cast_to_jsonb():
    """Should cast fingerprint, tags, extra and payload — sent as JSON text."""
    result = postgres_store.INSERT.count("%s::jsonb")
    expected = 4

    assert result == expected


def test_partitions_are_created_only_when_absent():
    """Should use IF NOT EXISTS so ensure_partitions can run on every prune."""
    result = postgres_store.PARTITION.startswith("CREATE TABLE IF NOT EXISTS")

    assert result is True


# partition bound parsing


def test_a_range_partition_reports_its_upper_bound():
    """Should read the TO bound out of the catalog's partition expression."""
    result = postgres_store._upper_bound(FIRING)
    expected = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)

    assert result == expected


def test_an_open_ended_partition_has_no_upper_bound():
    """Should refuse to date a partition that runs to MAXVALUE."""
    expression = "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO (MAXVALUE)"

    result = postgres_store._upper_bound(expression)

    assert result is None


def test_a_default_partition_has_no_upper_bound():
    """Should refuse to date the catch-all partition — prune must not drop it."""
    result = postgres_store._upper_bound("DEFAULT")

    assert result is None


def test_a_partition_open_at_the_bottom_still_reports_its_upper_bound():
    """Should date a FROM MINVALUE partition by its TO bound alone."""
    expression = "FOR VALUES FROM (MINVALUE) TO ('2026-01-01 00:00:00+00')"

    result = postgres_store._upper_bound(expression)
    expected = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

    assert result == expected


# month arithmetic


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0, datetime.date(2026, 8, 1)),
        (1, datetime.date(2026, 9, 1)),
        (5, datetime.date(2027, 1, 1)),
        (-1, datetime.date(2026, 7, 1)),
        (-8, datetime.date(2025, 12, 1)),
        (-19, datetime.date(2025, 1, 1)),
    ],
)
def test_month_start_walks_across_year_boundaries(offset, expected):
    """Should land on the first of the month whatever the offset."""
    result = postgres_store._month_start(datetime.date(2026, 8, 17), offset)

    assert result == expected


# timestamp normalisation


def test_a_naive_timestamp_is_read_as_utc():
    """Should assume UTC rather than the process timezone for a naive value."""
    stamp = datetime.datetime(2026, 8, 4, 9, 12, 41)

    result = postgres_store._aware(stamp)
    expected = datetime.datetime(2026, 8, 4, 9, 12, 41, tzinfo=datetime.UTC)

    assert result == expected


def test_an_aware_timestamp_is_left_alone():
    """Should not shift a value that already carries an offset."""
    kyiv = datetime.timezone(datetime.timedelta(hours=3))
    stamp = datetime.datetime(2026, 8, 4, 12, 12, 41, tzinfo=kyiv)

    result = postgres_store._aware(stamp)

    assert result is stamp


# ensure_partitions


@test.override_settings(PANDORA_RETENTION_DAYS=90)
def test_ensure_partitions_covers_the_retention_window_backwards(postgres_event_store):
    """Should create partitions back to retention so a late event still lands."""
    oldest = timezone.now().date() - datetime.timedelta(days=90)

    postgres_event_store.ensure_partitions()

    result = partition_name(oldest) in partitions(postgres_event_store)
    assert result is True


@test.override_settings(PANDORA_RETENTION_DAYS=90)
def test_ensure_partitions_stops_at_the_retention_window(postgres_event_store):
    """Should not create partitions for months that prune would drop anyway."""
    expired = month_before(timezone.now().date() - datetime.timedelta(days=90), 1)

    postgres_event_store.ensure_partitions()

    result = partition_name(expired) in partitions(postgres_event_store)
    assert result is False


def test_ensure_partitions_reaches_the_requested_months_ahead(postgres_event_store):
    """Should create every month up to the requested horizon."""
    horizon = month_after(timezone.now().date(), 4)

    postgres_event_store.ensure_partitions(months_ahead=4)

    result = partition_name(horizon) in partitions(postgres_event_store)
    assert result is True


def test_ensure_partitions_stops_at_the_requested_horizon(postgres_event_store):
    """Should not run further ahead than asked."""
    beyond = month_after(timezone.now().date(), 3)

    postgres_event_store.ensure_partitions(months_ahead=2)

    result = partition_name(beyond) in partitions(postgres_event_store)
    assert result is False


@test.override_settings(PANDORA_RETENTION_DAYS=0)
def test_ensure_partitions_does_not_recreate_a_pruned_month(postgres_event_store):
    """Should leave dropped months dropped — prune and ensure must not churn."""
    expired = month_before(timezone.now().date(), 1)
    postgres_event_store.prune(timezone.now())

    postgres_event_store.ensure_partitions()

    result = partition_name(expired) in partitions(postgres_event_store)
    assert result is False


def test_an_event_outside_every_partition_is_rejected(postgres_event_store, moment):
    """Should fail loudly rather than silently drop an event with no partition."""
    stray = support.make_event(
        0, moment, timestamp=moment + datetime.timedelta(days=3650)
    )

    with pytest.raises(db.Error) as error, transaction.atomic():
        postgres_event_store.insert([stray])

    assert "no partition" in str(error.value)


# prune granularity


def test_prune_drops_the_expired_partition(postgres_event_store, moment):
    """Should remove the whole month as one DDL statement, not row by row."""
    old = support.make_event(0, moment, timestamp=support.inside_previous_month(moment))
    postgres_event_store.insert([old])
    before = partitions(postgres_event_store)

    postgres_event_store.prune(support.month_start(moment))

    result = sorted(set(before) - set(partitions(postgres_event_store)))
    expected = [partition_name(support.inside_previous_month(moment))]
    assert result == expected


def test_prune_keeps_a_month_the_cutoff_falls_inside(postgres_event_store, moment):
    """Should keep an event older than the cutoff while its month is still live."""
    older = support.make_event(
        0, moment, timestamp=moment - datetime.timedelta(hours=1)
    )
    postgres_event_store.insert([older])

    removed = postgres_event_store.prune(moment)

    result = [removed, support.ids(postgres_event_store.fetch(1))]
    expected = [0, [support.event_id(0)]]
    assert result == expected


def test_prune_leaves_a_partition_it_cannot_date(postgres_event_store, moment):
    """Should never drop the catch-all partition, whatever the cutoff."""
    with postgres_event_store.connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE events_event_default PARTITION OF events_event DEFAULT"
        )

    postgres_event_store.prune(moment + datetime.timedelta(days=3650))

    result = partitions(postgres_event_store)
    expected = ["events_event_default"]
    assert result == expected


# identity


def test_the_primary_key_spans_id_and_timestamp(postgres_event_store, moment):
    """Should keep both rows — a partitioned table cannot key on id alone."""
    first = support.make_event(0, moment)
    second = support.make_event(
        0, moment, timestamp=moment + datetime.timedelta(hours=2)
    )

    postgres_event_store.insert([first, second])

    result = len(postgres_event_store.fetch(1))
    expected = 2
    assert result == expected
