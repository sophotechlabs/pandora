import pytest
from django import db
from django.db.utils import ConnectionHandler
from prometheus_client import REGISTRY

from pandora.core import database

GAUGE = "pandora_database_bytes"


def connection_for(vendor):
    for alias in db.connections:
        candidate = db.connections[alias]
        if candidate.vendor == vendor:
            return candidate
    return None


def require(vendor):
    connection = connection_for(vendor)
    if connection is None:
        pytest.skip(f"no {vendor} connection in this run — set TEST_DATABASE_URL")
    return connection


@pytest.fixture
def standalone(tmp_path, django_db_blocker):
    path = tmp_path / "standalone.sqlite3"
    handler = ConnectionHandler(
        {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(path),
            }
        }
    )
    connection = handler["default"]
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE sample (id integer primary key, body text)")
            cursor.execute("INSERT INTO sample (body) VALUES ('x')")
        yield connection
        connection.close()


class Stub:
    def __init__(self, vendor, name=""):
        self.vendor = vendor
        self.settings_dict = {"NAME": name}


@pytest.mark.parametrize("name", sorted(database.MEMORY_NAMES))
def test_sqlite_path_is_none_for_an_in_memory_database(name):
    """Should report no file when there is no file to back up."""
    result = database.sqlite_path(Stub("sqlite", name))

    assert result is None


def test_sqlite_path_is_none_for_a_shared_cache_uri():
    """Should report no file for the URI form Django's test runner substitutes."""
    uri = "file:memorydb_default?mode=memory&cache=shared"

    result = database.sqlite_path(Stub("sqlite", uri))

    assert result is None


def test_sqlite_path_names_the_file_behind_the_connection(standalone):
    """Should point at the database file so a backup knows what it copied."""
    result = database.sqlite_path(standalone)
    expected = standalone.settings_dict["NAME"]

    assert str(result) == expected


def test_sqlite_path_is_none_for_another_vendor():
    """Should refuse to invent a path for a database that has no file."""
    result = database.sqlite_path(Stub("postgresql", "pandora"))

    assert result is None


@pytest.mark.django_db(databases="__all__")
def test_size_bytes_reports_a_positive_size():
    """Should measure the live database rather than guess at it."""
    connection = require("sqlite")

    result = database.size_bytes(connection)

    assert result > 0


@pytest.mark.django_db(databases="__all__")
def test_size_bytes_reports_a_positive_size_on_postgres():
    """Should read pg_database_size so the gauge means the same on both."""
    connection = require("postgresql")

    result = database.size_bytes(connection)

    assert result > 0


def test_size_bytes_is_zero_for_an_unknown_vendor():
    """Should stay quiet rather than raise on a backend it cannot measure."""
    result = database.size_bytes(Stub("oracle"))

    assert result == 0


@pytest.mark.django_db(databases="__all__")
def test_refresh_size_publishes_the_gauge():
    """Should give Prometheus the number the retention alert is written against."""
    connection = require("sqlite")

    written = database.refresh_size(connection)

    result = REGISTRY.get_sample_value(GAUGE)
    assert result == written
    assert written > 0


@pytest.mark.django_db(databases="__all__")
def test_incremental_vacuum_runs_on_sqlite():
    """Should hand freed pages back so deletes actually shrink the file."""
    connection = require("sqlite")

    result = database.incremental_vacuum(connection)

    assert result is True


@pytest.mark.django_db(databases="__all__")
def test_incremental_vacuum_is_skipped_on_postgres():
    """Should do nothing where autovacuum already owns the job."""
    connection = require("postgresql")

    result = database.incremental_vacuum(connection)

    assert result is False


def test_vacuum_into_writes_a_readable_snapshot(standalone, tmp_path):
    """Should produce a consistent copy that opens on its own."""
    target = tmp_path / "snapshot.sqlite3"

    written = database.vacuum_into(standalone, target)

    handler = ConnectionHandler(
        {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(target),
            }
        }
    )
    copy = handler["default"]
    with copy.cursor() as cursor:
        cursor.execute("SELECT body FROM sample")
        rows = cursor.fetchall()
    copy.close()

    result = (written > 0, rows)
    expected = (True, [("x",)])

    assert result == expected


def test_incremental_vacuum_is_skipped_on_another_vendor():
    """Should leave a backend that reclaims its own pages alone."""
    result = database.incremental_vacuum(Stub("postgresql"))

    assert result is False
