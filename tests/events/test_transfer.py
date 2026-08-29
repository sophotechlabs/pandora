import datetime
import importlib
import io

import pytest
from django.core import management
from django.db.utils import ConnectionHandler
from django.utils import timezone

from pandora.events import store, transfer
from tests.events import support

INITIAL = importlib.import_module("pandora.events.migrations.0001_initial")
PAYLOAD = importlib.import_module("pandora.events.migrations.0002_event_payload")


class FakeStore:
    def __init__(self, events=()):
        self.rows = {event.id: event for event in events}
        self.pages = 0

    def insert(self, events):
        for event in events:
            self.rows.setdefault(event.id, event)

    def fetch(
        self, project_id, *, issue_id=None, episode_id=None, before=None, limit=100
    ):
        self.pages += 1
        ordered = sorted(self.rows.values(), key=lambda event: event.id, reverse=True)
        wanted = [event for event in ordered if event.project_id == project_id]
        if before is not None:
            wanted = [event for event in wanted if event.id < before]
        return wanted[:limit]


@pytest.fixture
def moment():
    now = timezone.now()
    floor = support.month_start(now) + datetime.timedelta(hours=6)
    return max(now - datetime.timedelta(hours=1), floor)


@pytest.fixture
def source_database_only(tmp_path, django_db_blocker):
    path = tmp_path / "empty-source.sqlite3"
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
            cursor.execute(INITIAL.SQLITE_TABLE)
            cursor.execute(PAYLOAD.SQLITE_ADD)
        connection.close()
    return f"sqlite:///{path}"


@pytest.fixture
def source_database(tmp_path, django_db_blocker, moment, project):
    path = tmp_path / "source.sqlite3"
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
            cursor.execute(INITIAL.SQLITE_TABLE)
            cursor.execute(PAYLOAD.SQLITE_ADD)
        source = store.get_store(connection)
        source.insert(support.make_events(5, moment, project_id=project.pk))
        connection.close()
    return f"sqlite:///{path}"


def test_transfer_copies_every_event(moment):
    """Should move the whole history, not the page it happened to read first."""
    events = support.make_events(7, moment)
    source = FakeStore(events)
    target = FakeStore()

    report = transfer.transfer(source, target, [1], batch=100)

    result = (sorted(target.rows), report.events, report.projects)
    expected = (sorted(support.ids(events)), 7, 1)

    assert result == expected


def test_transfer_pages_through_a_long_history(moment):
    """Should keep reading past the first batch instead of stopping at it."""
    events = support.make_events(10, moment)
    source = FakeStore(events)
    target = FakeStore()

    report = transfer.transfer(source, target, [1], batch=3)

    result = (len(target.rows), report.events, source.pages)
    expected = (10, 10, 4)

    assert result == expected


def test_transfer_is_safe_to_run_twice(moment):
    """Should let an interrupted migration be restarted without double counting."""
    events = support.make_events(4, moment)
    source = FakeStore(events)
    target = FakeStore()

    transfer.transfer(source, target, [1], batch=2)
    transfer.transfer(source, target, [1], batch=2)

    result = len(target.rows)
    expected = 4

    assert result == expected


def test_transfer_reports_an_empty_project(moment):
    """Should return a zero rather than fail when a project stored nothing."""
    source = FakeStore()
    target = FakeStore()

    report = transfer.transfer(source, target, [1, 2], batch=10)

    result = (report.events, report.projects)
    expected = (0, 2)

    assert result == expected


def test_transfer_skips_another_project(moment):
    """Should scope each page to the project it was asked for."""
    mine = support.make_events(3, moment)
    theirs = [
        support.make_event(index, moment, id=support.event_id(index), project_id=2)
        for index in (10, 11)
    ]
    source = FakeStore([*mine, *theirs])
    target = FakeStore()

    transfer.transfer(source, target, [1], batch=10)

    result = sorted(target.rows)
    expected = sorted(support.ids(mine))

    assert result == expected


@pytest.mark.django_db(databases="__all__")
def test_the_command_copies_events_out_of_another_database(
    source_database,
    project,
):
    """Should read the old database over a url and land the events in this one."""
    out = io.StringIO()

    management.call_command("transfer_events", "--from", source_database, stdout=out)

    target = store.get_store()
    result = len(target.fetch(project.pk))
    expected = 5

    assert result == expected
    assert "copied 5 events across 1 projects" in out.getvalue()


@pytest.mark.django_db(databases="__all__")
def test_the_command_rejects_a_url_it_cannot_read(project):
    """Should name the bad url rather than fail deep inside the driver."""
    with pytest.raises(management.CommandError, match="could not read"):
        management.call_command("transfer_events", "--from", "not-a-url")


@pytest.mark.django_db(databases="__all__")
def test_the_command_stops_when_no_project_was_restored(source_database_only):
    """Should refuse to run before loaddata rather than silently copy nothing."""
    with pytest.raises(management.CommandError, match="holds no projects"):
        management.call_command("transfer_events", "--from", source_database_only)


@pytest.mark.django_db(databases="__all__")
def test_the_command_rejects_a_batch_below_one(source_database):
    """Should refuse a page size that would never advance the cursor."""
    with pytest.raises(management.CommandError, match="--batch"):
        management.call_command(
            "transfer_events",
            "--from",
            source_database,
            "--batch",
            "0",
        )
