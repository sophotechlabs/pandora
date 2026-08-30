import inspect

import pytest
from django import db
from django.core import exceptions

from pandora.events import postgres_store, sqlite_store, store

METHODS = (
    "insert",
    "reassign",
    "reassign_events",
    "fetch",
    "search",
    "rewrite",
    "delete",
    "prune",
    "thin",
    "ensure_partitions",
)
IMPLEMENTATIONS = (sqlite_store.SqliteEventStore, postgres_store.PostgresEventStore)


class FakeConnection:
    def __init__(self, vendor):
        self.vendor = vendor


# interface contract


def test_the_protocol_pins_its_operations():
    """Should expose exactly the EventStore operations the phases agreed on."""
    result = sorted(name for name in vars(store.EventStore) if not name.startswith("_"))
    expected = sorted(METHODS)

    assert result == expected


@pytest.mark.parametrize("method", METHODS)
def test_both_stores_match_the_protocol_signature(method):
    """Should keep every implementation's signature identical to the protocol."""
    expected = inspect.signature(getattr(store.EventStore, method))
    result = [inspect.signature(getattr(impl, method)) for impl in IMPLEMENTATIONS]

    assert result == [expected, expected]


def test_fetch_keeps_its_cursor_arguments_keyword_only():
    """Should force issue_id, episode_id, before and limit to be passed by name."""
    parameters = inspect.signature(store.EventStore.fetch).parameters

    result = [
        (name, parameter.kind.name, parameter.default)
        for name, parameter in parameters.items()
    ]
    expected = [
        ("self", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("project_id", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("issue_id", "KEYWORD_ONLY", None),
        ("episode_id", "KEYWORD_ONLY", None),
        ("before", "KEYWORD_ONLY", None),
        ("limit", "KEYWORD_ONLY", 100),
    ]
    assert result == expected


def test_both_stores_target_one_table():
    """Should point both implementations at the table the migration creates."""
    result = [impl.table for impl in IMPLEMENTATIONS]
    expected = ["events_event", "events_event"]

    assert result == expected


# factory tests


def test_sqlite_gets_the_sqlite_store():
    """Should select the SQLite implementation for a sqlite connection."""
    result = store.get_store(FakeConnection("sqlite"))

    assert isinstance(result, sqlite_store.SqliteEventStore)


def test_postgres_gets_the_postgres_store():
    """Should select the Postgres implementation for a postgresql connection."""
    result = store.get_store(FakeConnection("postgresql"))

    assert isinstance(result, postgres_store.PostgresEventStore)


def test_the_store_keeps_the_connection_it_was_built_with():
    """Should bind the given connection so callers can target a replica later."""
    connection = FakeConnection("sqlite")

    result = store.get_store(connection)

    assert result.connection is connection


def test_an_unsupported_vendor_fails_loudly():
    """Should refuse to guess a store for a vendor with no implementation."""
    with pytest.raises(exceptions.ImproperlyConfigured) as error:
        store.get_store(FakeConnection("oracle"))

    result = str(error.value)
    expected = "no EventStore implementation for database vendor 'oracle'"
    assert result == expected


@pytest.mark.django_db
def test_the_default_store_binds_the_active_connection():
    """Should fall back to the project's live database connection."""
    result = store.get_store()

    assert result.connection is db.connection


# pass-through behaviour


def test_sqlite_has_no_partitions_to_maintain():
    """Should make ensure_partitions a no-op on SQLite, not an error."""
    built = sqlite_store.SqliteEventStore(FakeConnection("sqlite"))

    result = built.ensure_partitions()

    assert result is None
