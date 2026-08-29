import datetime
import io

import pytest
from django import test
from django.conf import settings
from django.core import management
from django.utils import timezone

from pandora.am.management.commands import prune
from pandora.events import store
from pandora.ingest import models as ingest_models
from pandora.issues import models as issue_models
from tests.events import support

pytestmark = pytest.mark.django_db

NOTHING = prune.PruneResult(events=0, envelopes=0, processed_events=0, silences=0)
RETENTION_DAYS = settings.PANDORA_RETENTION_DAYS


@pytest.fixture
def moment():
    now = timezone.now()
    floor = support.month_start(now) + datetime.timedelta(hours=6)
    return max(now - datetime.timedelta(hours=1), floor)


@pytest.fixture
def event_store(db):
    return store.get_store()


def make_envelope(project, state, received_at):
    return ingest_models.RawEnvelope.objects.create(
        project=project,
        source="am",
        environment="p-mk1",
        payload={"version": "4", "alerts": []},
        received_at=received_at,
        state=state,
    )


def run_command():
    out = io.StringIO()
    management.call_command("prune", stdout=out)
    return out.getvalue()


# command contract


def test_prune_asks_for_two_months_of_partitions():
    """Should keep the horizon the Postgres DDL was born with."""
    result = prune.MONTHS_AHEAD
    expected = 2

    assert result == expected


def test_prune_reports_every_retention_class():
    """Should account for each thing it deletes, not a single total."""
    result = list(prune.PruneResult.__dataclass_fields__)
    expected = [
        "events",
        "envelopes",
        "processed_events",
        "silences",
        "hourly_stats",
        "activities",
        "counters",
    ]

    assert result == expected


def test_prune_on_an_empty_database_removes_nothing(moment):
    """Should be safe to run before anything has been ingested."""
    result = prune.prune_expired(moment)
    expected = NOTHING

    assert result == expected


# events


@test.override_settings(PANDORA_RETENTION_DAYS=0)
def test_prune_removes_events_past_retention(event_store, moment):
    """Should delete event payloads whose month has fallen out of retention."""
    expired = support.make_event(
        0, moment, timestamp=support.inside_previous_month(moment)
    )
    retained = support.make_event(1, moment)
    event_store.insert([expired, retained])

    result = prune.prune_expired(moment)

    expected = prune.PruneResult(events=1, envelopes=0, processed_events=0, silences=0)
    assert result == expected
    assert support.ids(event_store.fetch(1)) == [support.event_id(1)]


def test_prune_keeps_events_inside_retention(event_store, moment):
    """Should leave the retained window alone at the default 90 days."""
    event_store.insert(support.make_events(3, moment))

    result = prune.prune_expired(moment)

    assert result == NOTHING
    assert len(event_store.fetch(1)) == 3


# raw envelopes


def test_prune_removes_a_done_envelope_past_its_window(project, moment):
    """Should drop processed envelopes once the replay window has passed."""
    make_envelope(
        project,
        ingest_models.EnvelopeState.DONE,
        moment - datetime.timedelta(days=8),
    )

    result = prune.prune_expired(moment)

    expected = prune.PruneResult(events=0, envelopes=1, processed_events=0, silences=0)
    assert result == expected
    assert ingest_models.RawEnvelope.objects.exists() is False


def test_prune_keeps_a_done_envelope_inside_its_window(project, moment):
    """Should keep a recent envelope — it is still the replay source."""
    make_envelope(
        project,
        ingest_models.EnvelopeState.DONE,
        moment - datetime.timedelta(days=6),
    )

    result = prune.prune_expired(moment)

    assert result == NOTHING
    assert ingest_models.RawEnvelope.objects.count() == 1


@pytest.mark.parametrize(
    "state",
    [ingest_models.EnvelopeState.PENDING, ingest_models.EnvelopeState.FAILED],
)
def test_prune_keeps_unprocessed_envelopes_whatever_their_age(project, moment, state):
    """Should never delete work that has not been processed yet."""
    make_envelope(project, state, moment - datetime.timedelta(days=400))

    result = prune.prune_expired(moment)

    assert result == NOTHING
    assert ingest_models.RawEnvelope.objects.count() == 1


# processed event markers


def test_prune_removes_dedup_markers_past_retention(project, moment):
    """Should expire the SDK dedup markers with the events they guard."""
    ingest_models.ProcessedEvent.objects.create(
        project=project,
        event_id="a" * 32,
        seen_at=moment - datetime.timedelta(days=RETENTION_DAYS + 1),
    )

    result = prune.prune_expired(moment)

    expected = prune.PruneResult(events=0, envelopes=0, processed_events=1, silences=0)
    assert result == expected


def test_prune_keeps_dedup_markers_inside_retention(project, moment):
    """Should keep a marker while its event is still retained."""
    ingest_models.ProcessedEvent.objects.create(
        project=project,
        event_id="b" * 32,
        seen_at=moment - datetime.timedelta(days=RETENTION_DAYS - 1),
    )

    result = prune.prune_expired(moment)

    assert result == NOTHING
    assert ingest_models.ProcessedEvent.objects.count() == 1


# silences


def test_prune_removes_an_expired_silence_link(issue, moment):
    """Should clear the bookkeeping for a silence Alertmanager already dropped."""
    issue_models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="4f1c2a3b-0000-0000-0000-000000000001",
        expires_at=moment - datetime.timedelta(minutes=1),
    )

    result = prune.prune_expired(moment)

    expected = prune.PruneResult(events=0, envelopes=0, processed_events=0, silences=1)
    assert result == expected


def test_prune_keeps_a_live_silence_link(issue, moment):
    """Should keep the link while the silence is still in force."""
    issue_models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="4f1c2a3b-0000-0000-0000-000000000002",
        expires_at=moment + datetime.timedelta(hours=4),
    )

    result = prune.prune_expired(moment)

    assert result == NOTHING
    assert issue_models.SilenceLink.objects.count() == 1


# command output


def test_the_command_reports_an_empty_run():
    """Should print one line naming every retention class it touched."""
    result = run_command()
    expected = (
        "prune: 0 events, 0 envelopes, 0 processed events, 0 silences,"
        " 0 hourly stats, 0 activities, 0 ingest counters\n"
    )

    assert result == expected


def test_the_command_reports_what_it_removed(project, issue):
    """Should count the rows it deleted in the line it prints."""
    now = timezone.now()
    make_envelope(
        project,
        ingest_models.EnvelopeState.DONE,
        now - datetime.timedelta(days=30),
    )
    issue_models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="4f1c2a3b-0000-0000-0000-000000000003",
        expires_at=now - datetime.timedelta(hours=1),
    )

    result = run_command()
    expected = (
        "prune: 0 events, 1 envelopes, 0 processed events, 1 silences,"
        " 0 hourly stats, 0 activities, 0 ingest counters\n"
    )

    assert result == expected


# aggregates that would otherwise grow forever


def test_hourly_stats_past_retention_are_removed(project, issue):
    """Should drop sparkline buckets nothing can read — the window is 7 days."""
    now = timezone.now()
    issue_models.HourlyStat.objects.create(
        issue=issue,
        hour=now - datetime.timedelta(days=120),
        count=3,
    )
    issue_models.HourlyStat.objects.create(issue=issue, hour=now, count=1)

    result = prune.prune_expired(now)

    assert result.hourly_stats == 1
    assert issue_models.HourlyStat.objects.count() == 1


def test_activities_past_retention_are_removed(project, issue):
    """Should bound the audit feed rather than let a flapping alert grow it forever."""
    now = timezone.now()
    issue_models.IssueActivity.objects.create(
        issue=issue,
        kind=issue_models.ActivityKind.CREATED,
        at=now - datetime.timedelta(days=120),
    )
    issue_models.IssueActivity.objects.create(
        issue=issue,
        kind=issue_models.ActivityKind.REGRESSION,
        at=now,
    )

    result = prune.prune_expired(now)

    assert result.activities == 1
    assert issue_models.IssueActivity.objects.count() == 1


def test_episodes_are_never_pruned(project, issue, episode):
    """Should keep episodes permanently — regroup replays them after payloads are gone,
    which is why the implementation plan pins them as permanent."""
    issue_models.Episode.objects.filter(pk=episode.pk).update(
        starts_at=timezone.now() - datetime.timedelta(days=400)
    )

    prune.prune_expired(timezone.now())

    result = issue_models.Episode.objects.count()
    expected = 1

    assert result == expected


def test_tag_stats_are_never_pruned(project, issue):
    """Should leave the tag distribution alone — it is a capped aggregate, not a
    time series, so pruning by age would corrupt a live issue's sidebar."""
    issue_models.TagStat.objects.create(
        issue=issue, key="namespace", value="payments", count=9
    )

    prune.prune_expired(timezone.now())

    result = issue_models.TagStat.objects.count()
    expected = 1

    assert result == expected


# reclaiming the space the deletes freed


def test_prune_reclaims_freed_pages_and_publishes_the_size(monkeypatch):
    """Should hand freed pages back and republish the gauge the alert reads."""
    called = []
    monkeypatch.setattr(
        prune.database,
        "incremental_vacuum",
        lambda connection=None: called.append("vacuum"),
    )
    monkeypatch.setattr(
        prune.database,
        "refresh_size",
        lambda connection=None: called.append("refresh"),
    )

    prune.prune_expired(timezone.now())

    result = called
    expected = ["vacuum", "refresh"]

    assert result == expected
