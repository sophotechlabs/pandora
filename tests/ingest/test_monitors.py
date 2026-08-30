import datetime
import http
import io
import json

import pytest
from django.core import management
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import monitors

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def key(project):
    return core_models.DsnKey.objects.create(project=project, public_key="k" * 32)


@pytest.fixture
def send(client, key):
    def post(slug="nightly-backup", body=None, public_key=None):
        url = f"/api/{key.project_id}/cron/{slug}/{public_key or key.public_key}/"
        return client.post(
            url,
            data=json.dumps(body or {}),
            content_type="application/json",
        )

    return post


# checking in


def test_a_check_in_creates_the_monitor(send, project):
    """Should need no configuration step — a job that reports is a job watched."""
    send()

    result = ingest_models.Monitor.objects.get().slug
    expected = "nightly-backup"

    assert result == expected


def test_a_check_in_marks_it_ok(send):
    """Should be the ordinary case, and it is one POST."""
    send(body={"status": "ok"})

    result = ingest_models.Monitor.objects.get().status
    expected = ingest_models.MonitorStatus.OK

    assert result == expected


def test_an_in_progress_check_in_records_the_start(send):
    """Should let the runtime limit mean something."""
    send(body={"status": "in_progress"})

    monitor = ingest_models.Monitor.objects.get()
    result = (monitor.status, monitor.last_started is not None)
    expected = (ingest_models.MonitorStatus.IN_PROGRESS, True)

    assert result == expected


def test_an_error_check_in_is_recorded(send):
    """Should distinguish 'it ran and failed' from 'it never ran'."""
    send(body={"status": "error"})

    result = ingest_models.Monitor.objects.get().status
    expected = ingest_models.MonitorStatus.ERROR

    assert result == expected


def test_a_schedule_can_be_declared_in_the_check_in(send):
    """Should let the job describe itself rather than a form describe it."""
    send(
        body={
            "status": "ok",
            "monitor_config": {
                "interval_minutes": 1440,
                "checkin_margin": 30,
                "max_runtime": 120,
            },
        }
    )

    monitor = ingest_models.Monitor.objects.get()
    result = (
        monitor.interval_minutes,
        monitor.margin_minutes,
        monitor.max_runtime_minutes,
    )
    expected = (1440, 30, 120)

    assert result == expected


def test_an_unknown_status_is_refused(send):
    """Should say so rather than record a check-in nobody can interpret."""
    response = send(body={"status": "probably"})

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_an_unknown_key_is_refused(send):
    """Should sit behind the same DSN key as the envelope door."""
    response = send(public_key="z" * 32)

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_an_empty_body_is_a_check_in(send):
    """Should accept the simplest possible curl, which is the point."""
    response = send(body={})

    result = (response.status_code, ingest_models.Monitor.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_a_get_is_refused(client, key):
    """Should be a POST, like every other door."""
    response = client.get(f"/api/{key.project_id}/cron/nightly/{key.public_key}/")

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_a_body_that_is_not_json_is_refused(client, key):
    """Should name the problem rather than silently record a check-in."""
    response = client.post(
        f"/api/{key.project_id}/cron/nightly/{key.public_key}/",
        data=b"not json",
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_an_oversized_check_in_is_refused(client, key):
    """Should hold the protocol's 100 KiB, which is generous for a check-in."""
    response = client.post(
        f"/api/{key.project_id}/cron/nightly/{key.public_key}/",
        data=json.dumps({"status": "ok", "note": "x" * 200000}),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


# the sweep


def test_a_monitor_that_missed_its_window_is_marked(project):
    """Should be the whole point — the thing that did not happen."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(hours=3))

    report = monitors.sweep(NOW)

    result = (report.missed, ingest_models.Monitor.objects.get().status)
    expected = (["nightly"], ingest_models.MonitorStatus.MISSED)

    assert result == expected


def test_a_monitor_inside_its_margin_is_left_alone(project):
    """Should give a job the margin it declared before calling it missed."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(minutes=10))

    report = monitors.sweep(NOW)

    result = report.missed
    expected = []

    assert result == expected


def test_a_job_running_longer_than_its_limit_is_marked(project):
    """Should catch the run that started and never finished."""
    monitors.check_in(
        project, "nightly", "in_progress", NOW - datetime.timedelta(hours=2)
    )

    report = monitors.sweep(NOW)

    result = (report.timed_out, ingest_models.Monitor.objects.get().status)
    expected = (["nightly"], ingest_models.MonitorStatus.TIMED_OUT)

    assert result == expected


def test_a_monitor_that_never_checked_in_is_not_missed(project):
    """Should not blame a monitor that has never reported at all."""
    ingest_models.Monitor.objects.create(project=project, slug="never")

    report = monitors.sweep(NOW)

    result = report.missed
    expected = []

    assert result == expected


def test_an_inactive_monitor_is_skipped(project):
    """Should let a monitor be parked without deleting it."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(hours=3))
    ingest_models.Monitor.objects.update(active=False)

    report = monitors.sweep(NOW)

    result = report.missed
    expected = []

    assert result == expected


def test_a_missed_monitor_is_not_reported_twice(project):
    """Should not re-announce the same missed window on every sweep."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(hours=3))
    monitors.sweep(NOW)

    report = monitors.sweep(NOW)

    result = report.missed
    expected = []

    assert result == expected


def test_a_check_in_after_a_miss_clears_it(project):
    """Should recover on its own when the job comes back."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(hours=3))
    monitors.sweep(NOW)

    monitors.check_in(project, "nightly", "ok", NOW)

    result = ingest_models.Monitor.objects.get().status
    expected = ingest_models.MonitorStatus.OK

    assert result == expected


def test_a_monitor_reads_as_its_schedule(project):
    """Should be legible in a list without opening the row."""
    monitor = monitors.check_in(project, "nightly", "ok", NOW)

    result = str(monitor)

    assert "nightly every 60m" in result


# the command


def test_the_command_sweeps_once(project):
    """Should be runnable as a cron job."""
    monitors.check_in(project, "nightly", "ok", NOW - datetime.timedelta(hours=3))
    out = io.StringIO()

    management.call_command("monitors", stdout=out)

    assert "nightly missed its window" in out.getvalue()


def test_the_command_reports_a_quiet_sweep(project):
    """Should say nothing happened rather than print nothing at all."""
    out = io.StringIO()

    management.call_command("monitors", stdout=out)

    assert "0 missed" in out.getvalue()


def test_a_monitor_slug_is_cleaned(project):
    """Should not let a path or spaces become a slug nobody can filter on."""
    monitor = monitors.check_in(project, "Nightly Backup!", "ok", NOW)

    result = monitor.slug
    expected = "nightly-backup"

    assert result == expected


def test_an_in_progress_monitor_without_a_start_is_not_timed_out(project):
    """Should not blame a monitor whose start time was lost."""
    monitors.check_in(project, "nightly", "in_progress", NOW)
    ingest_models.Monitor.objects.update(last_started=None)

    report = monitors.sweep(NOW)

    result = report.timed_out
    expected = []

    assert result == expected


def test_the_environment_is_carried_from_the_check_in(project):
    """Should let a monitor say which cluster it belongs to."""
    monitors.check_in(project, "nightly", "ok", NOW, environment="p-mk1")

    result = ingest_models.Monitor.objects.get().environment
    expected = "p-mk1"

    assert result == expected


def test_the_loop_can_be_stopped(project, monkeypatch):
    """Should keep sweeping until something stops it, like every other loop."""
    calls = {"count": 0}

    def stop(_seconds):
        calls["count"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", stop)

    with pytest.raises(KeyboardInterrupt):
        management.call_command("monitors", loop=1, stdout=io.StringIO())

    assert calls["count"] == 1


def test_the_command_reports_a_timed_out_job(project):
    """Should name the run that started and never came back."""
    monitors.check_in(
        project, "nightly", "in_progress", NOW - datetime.timedelta(hours=2)
    )
    out = io.StringIO()

    management.call_command("monitors", stdout=out)

    assert "over its runtime" in out.getvalue()


def test_the_cron_door_holds_the_gate(client, key, settings):
    """Should refuse an oversized body like every other door."""
    settings.PANDORA_INGEST_MAX_BYTES = 400

    response = client.post(
        f"/api/{key.project_id}/cron/nightly/{key.public_key}/",
        data=json.dumps({"status": "ok", "padding": "x" * 3000}),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected
