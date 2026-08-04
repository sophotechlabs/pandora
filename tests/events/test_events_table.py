import pytest
from django import db

pytestmark = pytest.mark.django_db

COLUMNS = (
    "id",
    "project_id",
    "issue_id",
    "episode_id",
    "fingerprint",
    "timestamp",
    "level",
    "message",
    "tags",
    "extra",
    "source",
    "environment",
)

INSERT_COLUMNS = 'id, project_id, "timestamp", level, message, source, environment'

POSTGRES_ONLY = pytest.mark.skipif(
    db.connection.vendor != "postgresql",
    reason="partitioning is the postgres branch of the vendor-branched DDL",
)


def quoted(columns):
    return ", ".join(f'"{column}"' for column in columns)


def insert_statement():
    if db.connection.vendor == "postgresql":
        return (
            f"INSERT INTO events_event ({INSERT_COLUMNS}) "
            "VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s)"
        )
    return (
        f"INSERT INTO events_event ({INSERT_COLUMNS}) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )


def row(event_id):
    return [
        event_id,
        1,
        "2026-08-04T09:12:41+00:00",
        "error",
        "Pod is crash looping.",
        "am",
        "p-mk1",
    ]


# schema contract


def test_the_events_table_carries_every_frozen_column():
    """Should create the table with exactly the columns the Event dataclass names."""
    with db.connection.cursor() as cursor:
        cursor.execute(f"SELECT {quoted(COLUMNS)} FROM events_event WHERE 1 = 0")
        result = [column[0] for column in cursor.description]

    expected = list(COLUMNS)
    assert result == expected


def test_the_events_table_holds_no_lifecycle_state():
    """Should keep triage and episode lifecycle columns out of the payload store."""
    with db.connection.cursor() as cursor:
        cursor.execute("SELECT * FROM events_event WHERE 1 = 0")
        result = {column[0] for column in cursor.description}

    assert result.isdisjoint({"triage_state", "source_state", "ends_at", "starts_at"})


def test_the_events_table_has_no_foreign_keys():
    """Should keep issue and episode as plain columns — the store stays swappable."""
    with db.connection.cursor() as cursor:
        constraints = db.connection.introspection.get_constraints(
            cursor,
            "events_event",
        )

    result = [
        name
        for name, definition in constraints.items()
        if definition.get("foreign_key") is not None
    ]
    expected = []

    assert result == expected


# write path


def test_a_row_written_now_lands_and_reads_back():
    """Should accept an insert stamped now — partitions must cover today."""
    written = row("01J0000000000000000000000A")

    with db.connection.cursor() as cursor:
        cursor.execute(insert_statement(), written)
        cursor.execute(
            "SELECT level, message, environment FROM events_event WHERE id = %s",
            [written[0]],
        )
        result = cursor.fetchone()

    expected = ("error", "Pod is crash looping.", "p-mk1")
    assert result == expected


def test_the_json_columns_default_to_empty_containers():
    """Should default fingerprint, tags and extra so a minimal insert is valid."""
    written = row("01J0000000000000000000000B")

    with db.connection.cursor() as cursor:
        cursor.execute(insert_statement(), written)
        cursor.execute(
            "SELECT fingerprint, tags, extra FROM events_event WHERE id = %s",
            [written[0]],
        )
        fingerprint, tags, extra = cursor.fetchone()

    result = [str(fingerprint), str(tags), str(extra)]
    expected = ["[]", "{}", "{}"]
    assert result == expected


# postgres partitioning


@POSTGRES_ONLY
def test_postgres_gets_a_range_partitioned_parent():
    """Should create events_event as a partitioned parent, not a plain table."""
    with db.connection.cursor() as cursor:
        cursor.execute("SELECT relkind FROM pg_class WHERE relname = 'events_event'")
        result = cursor.fetchone()[0]

    expected = "p"
    assert result == expected


@POSTGRES_ONLY
def test_postgres_partitions_by_the_timestamp_column():
    """Should range-partition on timestamp so prune can drop whole months."""
    with db.connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_partkeydef('events_event'::regclass)")
        result = cursor.fetchone()[0]

    assert result.startswith("RANGE")
    assert "timestamp" in result


@POSTGRES_ONLY
def test_postgres_starts_with_monthly_partitions_around_today():
    """Should ship partitions covering last month through two months ahead."""
    with db.connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_inherits "
            "WHERE inhparent = 'events_event'::regclass"
        )
        result = cursor.fetchone()[0]

    expected = 4
    assert result == expected


@POSTGRES_ONLY
def test_postgres_keys_rows_by_id_and_timestamp():
    """Should use the composite primary key a partitioned table requires."""
    with db.connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'events_event'::regclass AND i.indisprimary "
            "ORDER BY a.attname"
        )
        result = [name for (name,) in cursor.fetchall()]

    expected = ["id", "timestamp"]
    assert result == expected
