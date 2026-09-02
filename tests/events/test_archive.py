import datetime
import gzip
import io
import json
from pathlib import Path

import pytest
from django.core import management
from django.core.management.base import CommandError
from django.utils import timezone

from pandora.events import archive
from tests.events import support

pytestmark = pytest.mark.django_db

NOW = timezone.now().replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def stored(project):
    from pandora.events.store import get_store

    store = get_store()
    counter = {"index": 0}

    def build(count=3, hour=None):
        moment = hour or NOW
        events = []
        for _ in range(count):
            counter["index"] += 1
            events.append(
                support.make_event(
                    counter["index"],
                    moment,
                    project_id=project.pk,
                    issue_id=None,
                    episode_id=None,
                    timestamp=moment + datetime.timedelta(seconds=counter["index"]),
                )
            )
        store.insert(events)
        return store, events

    return build


# the path


def test_the_key_is_hive_partitioned():
    """Should be a prefix any query engine already knows how to read."""
    result = archive.key_for(7, datetime.datetime(2026, 8, 30, 14, tzinfo=datetime.UTC))
    expected = "project=7/year=2026/month=08/day=30/hour=14/events.jsonl.gz"

    assert result == expected


def test_no_destination_means_no_export(project, settings):
    """Should do nothing rather than guess where to write."""
    settings.PANDORA_ARCHIVE_DIR = ""

    result = archive.export(project.pk, NOW, NOW + datetime.timedelta(hours=1))

    assert result.files == []


# writing


def test_an_hour_of_events_becomes_one_file(project, stored, tmp_path):
    """Should be one object per hour, which is what makes a restore cheap."""
    store, _ = stored()

    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    result = (len(report.files), report.events)
    expected = (1, 3)

    assert result == expected


def test_the_file_is_gzipped_json_lines(project, stored, tmp_path):
    """Should be readable with zcat and jq, not with a client library."""
    store, _ = stored()
    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    body = gzip.decompress(Path(report.files[0].path).read_bytes()).decode()
    rows = [json.loads(line) for line in body.splitlines()]

    result = len(rows)
    expected = 3

    assert result == expected


def test_every_field_of_the_event_survives(project, stored, tmp_path):
    """Should be a full record, not a summary — it is the backup."""
    store, events = stored(count=1)
    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    body = gzip.decompress(Path(report.files[0].path).read_bytes()).decode()
    row = json.loads(body)

    assert row["id"] == events[0].id
    assert row["message"] == events[0].message


def test_an_empty_hour_writes_nothing(project, stored, tmp_path):
    """Should not litter the bucket with empty objects."""
    store, _ = stored()

    report = archive.export(
        project.pk,
        NOW + datetime.timedelta(hours=5),
        NOW + datetime.timedelta(hours=6),
        store=store,
        destination=tmp_path,
    )

    assert report.files == []


def test_two_hours_become_two_files(project, stored, tmp_path):
    """Should partition by hour, not by run."""
    store, _ = stored(count=1)
    stored(count=1, hour=NOW + datetime.timedelta(hours=1))

    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=2),
        store=store,
        destination=tmp_path,
    )

    result = len(report.files)
    expected = 2

    assert result == expected


def test_the_report_reads_as_a_list_of_files(project, stored, tmp_path):
    """Should say what it wrote, so a cron log is worth reading."""
    store, _ = stored()
    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    assert "3 event(s)" in report.lines()[0]


def test_resume_skips_an_hour_already_written(project, stored, tmp_path):
    store, _ = stored(count=1)
    archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )
    stored(count=1)

    report = archive.resume(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    path = tmp_path / archive.key_for(project.pk, NOW)
    rows = gzip.decompress(path.read_bytes()).decode().splitlines()
    assert report.events == 0
    assert report.skipped == [str(path)]
    assert len(rows) == 1


def test_a_normal_rerun_rewrites_the_hour_for_late_events(project, stored, tmp_path):
    store, _ = stored(count=1)
    archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )
    stored(count=1)

    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=1),
        store=store,
        destination=tmp_path,
    )

    path = Path(report.files[0].path)
    rows = gzip.decompress(path.read_bytes()).decode().splitlines()
    assert len(rows) == 2


def test_an_archive_file_is_published_atomically(project, stored, tmp_path, mocker):
    store, _ = stored(count=1)
    replace = mocker.patch.object(Path, "replace", side_effect=OSError("full"))

    with pytest.raises(OSError, match="full"):
        archive.export(
            project.pk,
            NOW,
            NOW + datetime.timedelta(hours=1),
            store=store,
            destination=tmp_path,
        )

    final = tmp_path / archive.key_for(project.pk, NOW)
    assert replace.call_count == 1
    assert final.exists() is False
    assert list(final.parent.glob("*.tmp")) == []


# the command


def test_the_command_needs_somewhere_to_write(project, settings):
    """Should fail on the argument rather than write into the working directory."""
    settings.PANDORA_ARCHIVE_DIR = ""

    with pytest.raises(CommandError, match="PANDORA_ARCHIVE_DIR"):
        management.call_command("archive", stdout=io.StringIO())


def test_the_command_refuses_an_unknown_project(project, tmp_path):
    """Should catch the typo before writing nothing and reporting success."""
    with pytest.raises(CommandError, match="no project called"):
        management.call_command(
            "archive", project="nothing", to=str(tmp_path), stdout=io.StringIO()
        )


def test_the_command_refuses_a_bad_timestamp(project, tmp_path):
    """Should name the argument that is wrong."""
    with pytest.raises(CommandError, match="ISO 8601"):
        management.call_command(
            "archive", since="yesterday", to=str(tmp_path), stdout=io.StringIO()
        )


def test_the_command_refuses_a_backwards_window(project, tmp_path):
    with pytest.raises(CommandError, match="--since must be before --until"):
        management.call_command(
            "archive",
            since=NOW.isoformat(),
            until=(NOW - datetime.timedelta(hours=1)).isoformat(),
            to=str(tmp_path),
            stdout=io.StringIO(),
        )


def test_the_command_is_recorded_in_the_history(project, tmp_path):
    """Should be answerable later: when was this window last archived."""
    from pandora.people.models import AuditEntry

    management.call_command("archive", to=str(tmp_path), stdout=io.StringIO())

    result = AuditEntry.objects.filter(action="events.archive").count()
    expected = 1

    assert result == expected


def test_the_default_destination_comes_from_the_setting(project, settings, tmp_path):
    """Should let a cron job run with no arguments once it is configured."""
    settings.PANDORA_ARCHIVE_DIR = str(tmp_path)

    result = archive.root()
    expected = tmp_path

    assert result == expected


def test_more_events_than_a_page_are_all_exported(project, stored, tmp_path):
    """Should page through the store rather than stop at the first batch."""
    store, _ = stored(count=archive.PAGE + 5)

    report = archive.export(
        project.pk,
        NOW,
        NOW + datetime.timedelta(hours=2),
        store=store,
        destination=tmp_path,
    )

    result = report.events
    expected = archive.PAGE + 5

    assert result == expected


def test_the_command_writes_where_it_was_told(project, tmp_path, settings):
    """Should take --to over the setting, so one run can go somewhere else."""
    settings.PANDORA_ARCHIVE_DIR = "/nowhere"

    management.call_command("archive", to=str(tmp_path), stdout=io.StringIO())

    assert tmp_path.exists()


def test_the_command_takes_a_window(project, stored, tmp_path):
    """Should archive a named range, which is what a backfill needs."""
    stored(count=2)
    out = io.StringIO()

    management.call_command(
        "archive",
        since=(NOW - datetime.timedelta(hours=1)).isoformat(),
        until=(NOW + datetime.timedelta(hours=1)).isoformat(),
        to=str(tmp_path),
        stdout=out,
    )

    assert "2 event(s)" in out.getvalue()


def test_the_command_can_be_scoped_to_one_project(project, stored, tmp_path):
    """Should let a big install archive one project at a time."""
    stored(count=1)
    out = io.StringIO()

    management.call_command(
        "archive", project="infrastructure", to=str(tmp_path), stdout=out
    )

    assert "1 file(s)" in out.getvalue()


def test_the_command_can_resume_a_backfill(project, stored, tmp_path):
    stored(count=1)
    options = {
        "project": "infrastructure",
        "since": NOW.isoformat(),
        "until": (NOW + datetime.timedelta(hours=1)).isoformat(),
        "to": str(tmp_path),
    }
    management.call_command("archive", stdout=io.StringIO(), **options)
    out = io.StringIO()

    management.call_command("archive", resume=True, stdout=out, **options)

    assert "0 file(s), 0 event(s), 1 skipped" in out.getvalue()
