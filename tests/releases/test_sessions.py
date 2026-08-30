import datetime
import json

import pytest
from django.utils import timezone

from pandora.releases import models as release_models
from pandora.releases import sessions

pytestmark = pytest.mark.django_db

NOW = timezone.now().replace(minute=0, second=0, microsecond=0)


def one(status="exited", errors=0, release="1.2.3", environment="p-mk1", started=None):
    return {
        "sid": "s1",
        "status": status,
        "errors": errors,
        "started": (started or NOW).isoformat(),
        "attrs": {"release": release, "environment": environment},
    }


def aggregated(**counts):
    return {
        "attrs": {"release": "1.2.3", "environment": "p-mk1"},
        "aggregates": [{"started": NOW.isoformat(), **counts}],
    }


# taking sessions


def test_one_healthy_session_is_counted(project):
    """Should be the denominator of every crash-free number."""
    sessions.accept(project, one(), NOW)

    row = release_models.SessionBucket.objects.get()
    result = (row.sessions, row.crashed)
    expected = (1, 0)

    assert result == expected


def test_a_crashed_session_is_counted_as_crashed(project):
    """Should be the numerator, and it is the only status that hurts the rate."""
    sessions.accept(project, one(status="crashed"), NOW)

    result = release_models.SessionBucket.objects.get().crashed
    expected = 1

    assert result == expected


def test_an_errored_session_is_not_a_crash(project):
    """Should be the distinction BugSnag built a product on."""
    sessions.accept(project, one(errors=3), NOW)

    row = release_models.SessionBucket.objects.get()
    result = (row.errored, row.crashed)
    expected = (1, 0)

    assert result == expected


def test_an_abnormal_session_is_its_own_status(project):
    """Should not be folded into crashed — the process did not report a crash."""
    sessions.accept(project, one(status="abnormal"), NOW)

    result = release_models.SessionBucket.objects.get().abnormal
    expected = 1

    assert result == expected


def test_sessions_in_the_same_hour_share_a_bucket(project):
    """Should be a counter per hour, not a row per session."""
    sessions.accept(project, one(), NOW)
    sessions.accept(project, one(), NOW)

    row = release_models.SessionBucket.objects.get()
    result = (release_models.SessionBucket.objects.count(), row.sessions)
    expected = (1, 2)

    assert result == expected


def test_two_hours_are_two_buckets(project):
    """Should keep the shape a rate is computed over."""
    sessions.accept(project, one(), NOW)
    sessions.accept(project, one(started=NOW - datetime.timedelta(hours=2)), NOW)

    result = release_models.SessionBucket.objects.count()
    expected = 2

    assert result == expected


def test_a_pre_aggregated_bucket_is_taken_whole(project):
    """Should accept what a server SDK sends instead of one item per session."""
    sessions.accept(project, aggregated(exited=90, crashed=10), NOW)

    row = release_models.SessionBucket.objects.get()
    result = (row.sessions, row.crashed)
    expected = (100, 10)

    assert result == expected


def test_an_empty_aggregate_is_skipped(project):
    """Should not write a row that counts nothing."""
    sessions.accept(project, aggregated(exited=0), NOW)

    result = release_models.SessionBucket.objects.count()
    expected = 0

    assert result == expected


def test_something_that_is_not_a_session_is_ignored(project):
    """Should ack and drop rather than raise on a payload it cannot read."""
    result = sessions.accept(project, ["not", "a", "session"], NOW)
    expected = 0

    assert result == expected


def test_a_bucket_reads_as_its_hour_and_count(project):
    """Should be legible in the admin without opening the row."""
    sessions.accept(project, one(), NOW)

    result = str(release_models.SessionBucket.objects.get())

    assert "1.2.3" in result and "x1" in result


# the numbers


def test_crash_free_is_one_when_nothing_crashed(project):
    """Should be the number people put on a dashboard."""
    sessions.accept(project, aggregated(exited=100), NOW)

    result = sessions.health(project, "1.2.3").crash_free
    expected = 1.0

    assert result == expected


def test_crash_free_falls_with_crashes(project):
    """Should move with the thing it measures."""
    sessions.accept(project, aggregated(exited=90, crashed=10), NOW)

    result = sessions.health(project, "1.2.3").crash_free_percent
    expected = 90.0

    assert result == expected


def test_an_unseen_release_is_crash_free(project):
    """Should not divide by zero on a release nothing has reported."""
    result = sessions.health(project, "9.9.9").crash_free
    expected = 1.0

    assert result == expected


def test_the_healthy_count_is_what_is_left(project):
    """Should add up: healthy, errored, abnormal and crashed are the whole."""
    sessions.accept(
        project, aggregated(exited=80, errored=10, abnormal=5, crashed=5), NOW
    )

    result = sessions.health(project, "1.2.3").healthy
    expected = 80

    assert result == expected


def test_health_can_be_scoped_to_an_environment(project):
    """Should let production's number be read without staging in it."""
    sessions.accept(project, one(environment="p-mk1", status="crashed"), NOW)
    sessions.accept(project, one(environment="staging"), NOW)

    result = sessions.health(project, "1.2.3", environment="staging").crashed
    expected = 0

    assert result == expected


def test_adoption_is_this_release_against_everything(project):
    """Should be the fixed 24-hour window Sentry uses, and nothing cleverer."""
    sessions.accept(project, aggregated(exited=30), NOW)
    sessions.accept(
        project,
        {
            "attrs": {"release": "1.2.2", "environment": "p-mk1"},
            "aggregates": [{"started": NOW.isoformat(), "exited": 70}],
        },
        NOW,
    )

    result = round(sessions.adoption(project, "1.2.3", NOW), 2)
    expected = 0.3

    assert result == expected


def test_adoption_with_no_sessions_is_zero(project):
    """Should answer rather than divide by nothing."""
    result = sessions.adoption(project, "1.2.3", NOW)
    expected = 0.0

    assert result == expected


# through the door


def test_the_envelope_door_takes_a_session(client, dsn_key):
    """Should accept the item type rather than counting and dropping it."""
    body = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "session"}),
            json.dumps(one()),
        ]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = release_models.SessionBucket.objects.count()
    expected = 1

    assert result == expected


def test_a_session_never_reaches_the_event_store(client, dsn_key):
    """Should be counted, not recorded — that is the whole design decision."""
    from pandora.ingest import models as ingest_models

    body = "\n".join(
        [json.dumps({}), json.dumps({"type": "session"}), json.dumps(one())]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = ingest_models.RawEnvelope.objects.count()
    expected = 0

    assert result == expected


def test_aggregates_that_are_not_a_list_are_ignored(project):
    """Should ack and drop a malformed payload rather than raise."""
    result = sessions.accept(project, {"attrs": {}, "aggregates": {"exited": 5}}, NOW)
    expected = 0

    assert result == expected


def test_a_bucket_that_is_not_an_object_is_skipped(project):
    """Should take the good buckets out of a partly malformed payload."""
    payload = {
        "attrs": {"release": "1.2.3"},
        "aggregates": ["nonsense", {"started": NOW.isoformat(), "exited": 4}],
    }

    result = sessions.accept(project, payload, NOW)
    expected = 4

    assert result == expected


def test_a_session_with_no_start_time_lands_in_the_hour_it_arrived(project):
    """Should not drop a session because the SDK left the field out."""
    payload = {"sid": "s1", "status": "exited", "attrs": {"release": "1.2.3"}}

    sessions.accept(project, payload, NOW)

    result = release_models.SessionBucket.objects.get().hour
    expected = NOW

    assert result == expected


def test_a_session_item_that_is_not_json_is_dropped(client, dsn_key):
    """Should ack the envelope rather than fail it on one bad item."""
    body = b'{}\n{"type": "session"}\nnot json'

    response = client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    assert response.status_code == 200
    assert release_models.SessionBucket.objects.count() == 0


def test_a_start_time_that_is_already_a_datetime_is_used(project):
    """Should accept what a caller inside pandora hands it, not only wire JSON."""
    payload = {
        "sid": "s1",
        "status": "exited",
        "started": NOW,
        "attrs": {"release": "1.2.3"},
    }

    sessions.accept(project, payload, NOW + datetime.timedelta(hours=5))

    result = release_models.SessionBucket.objects.get().hour
    expected = NOW

    assert result == expected
