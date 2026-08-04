import datetime

import pytest

from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.issues import models as issue_models
from tests.ingest import fakes, helpers

RECEIVED_AT = datetime.datetime(2026, 8, 4, 9, 15, tzinfo=datetime.UTC)
LATER = datetime.datetime(2026, 8, 4, 9, 20, tzinfo=datetime.UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def store():
    return fakes.RecordingEventStore()


def wipe():
    issue_models.Issue.objects.all().delete()
    ingest_models.RawEnvelope.objects.all().delete()


# replaying one envelope


def test_replaying_an_envelope_leaves_the_database_untouched(am_fixture, token, store):
    """Should make a redelivered queue message a no-op, byte for byte."""
    envelope = helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    before = helpers.snapshot()

    processor.process_envelope(envelope.pk, store=store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_replaying_a_resolution_leaves_the_database_untouched(am_fixture, token, store):
    """Should keep a closed episode closed when its envelope is replayed."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    envelope = helpers.deliver(am_fixture("resolved_group"), token, store, LATER)
    before = helpers.snapshot()

    processor.process_envelope(envelope.pk, store=store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_replaying_a_whole_sequence_leaves_the_database_untouched(
    am_fixture, token, store
):
    """Should survive a full inbox replay after a restart."""
    first = helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    second = helpers.deliver(am_fixture("resolved_group"), token, store, LATER)
    before = helpers.snapshot()

    processor.process_envelope(first.pk, store=store)
    processor.process_envelope(second.pk, store=store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


# repeat deliveries from alertmanager


def test_a_repeat_delivery_never_counts_a_second_occurrence(am_fixture, token, store):
    """Should leave event_count where it was when repeat_interval fires again."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = issue_models.Issue.objects.get().event_count
    expected = 2

    assert result == expected


def test_a_repeat_delivery_never_opens_a_second_episode(am_fixture, token, store):
    """Should recognise the same episode by fingerprint and start."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = issue_models.Episode.objects.count()
    expected = 2

    assert result == expected


def test_a_repeat_delivery_moves_the_delivery_counters(am_fixture, token, store):
    """Should record that Alertmanager said the same thing again, and when."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = sorted(
        (episode.delivery_count, episode.last_delivery_at)
        for episode in issue_models.Episode.objects.all()
    )
    expected = [(2, LATER), (2, LATER)]

    assert result == expected


def test_a_repeat_delivery_leaves_the_aggregates_alone(am_fixture, token, store):
    """Should keep the sparkline honest — one bar per episode, not per delivery."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    before = sorted(
        (stat.hour, stat.count) for stat in issue_models.HourlyStat.objects.all()
    )

    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = sorted(
        (stat.hour, stat.count) for stat in issue_models.HourlyStat.objects.all()
    )
    expected = before

    assert result == expected


def test_a_repeat_delivery_leaves_the_tag_distribution_alone(am_fixture, token, store):
    """Should not inflate tag counts on every repeat_interval tick."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = issue_models.TagStat.objects.get(key="namespace", value="payments").count
    expected = 2

    assert result == expected


def test_a_repeat_delivery_keeps_the_creation_activity_single(am_fixture, token, store):
    """Should not log a creation for every delivery of the same group."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = issue_models.IssueActivity.objects.count()
    expected = 1

    assert result == expected


def test_a_repeat_delivery_writes_no_further_event_rows(am_fixture, token, store):
    """Should write a blob per episode change, never one per delivery."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = len(store.rows)
    expected = 2

    assert result == expected


def test_a_repeat_only_envelope_never_calls_the_event_store(am_fixture, token):
    """Should skip the store round trip when nothing about the episodes moved."""
    calls = []

    class CountingStore(fakes.RecordingEventStore):
        def insert(self, events):
            calls.append(len(events))
            super().insert(events)

    store = CountingStore()
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    result = calls
    expected = [2]

    assert result == expected


def test_replaying_an_envelope_reuses_its_event_identity(am_fixture, token, store):
    """Should let a replayed envelope collide with its own rows, not add new ones."""
    envelope = helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    before = sorted(row.id for row in store.rows)

    envelope.state = ingest_models.EnvelopeState.FAILED
    envelope.save(update_fields=["state"])
    processor.process_envelope(envelope.pk, store=store)

    result = sorted({row.id for row in store.rows})
    expected = before

    assert result == expected


# crash between the two stores


def test_a_crash_before_the_relational_write_leaves_nothing_behind(am_fixture, token):
    """Should roll the group back entirely when the event store dies mid-batch."""
    helpers.deliver(
        am_fixture("firing_group"), token, fakes.FailingEventStore(), RECEIVED_AT
    )

    result = helpers.snapshot()
    expected = {
        "issues": [],
        "episodes": [],
        "hourly": [],
        "tags": [],
        "activities": [],
    }

    assert result == expected


def test_a_replay_after_a_crash_reaches_the_clean_state(am_fixture, token):
    """Should land on exactly the state a run without the crash would have left."""
    flaky = fakes.FlakyEventStore(failures=1)
    envelope = helpers.deliver(am_fixture("firing_group"), token, flaky, RECEIVED_AT)
    processor.process_envelope(envelope.pk, store=flaky)
    crashed = helpers.snapshot()

    wipe()
    helpers.deliver(
        am_fixture("firing_group"),
        token,
        fakes.RecordingEventStore(),
        RECEIVED_AT,
    )

    result = helpers.snapshot()
    expected = crashed

    assert result == expected


def test_a_replay_after_a_crash_writes_the_events_once(am_fixture, token):
    """Should not double-write blobs for the occurrences the crash rolled back."""
    flaky = fakes.FlakyEventStore(failures=1)
    envelope = helpers.deliver(am_fixture("firing_group"), token, flaky, RECEIVED_AT)

    processor.process_envelope(envelope.pk, store=flaky)

    result = len(flaky.rows)
    expected = 2

    assert result == expected


def test_a_crash_mid_sequence_does_not_double_count_on_replay(am_fixture, token):
    """Should keep counters right when a resolution fails and is replayed."""
    store = fakes.RecordingEventStore()
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    envelope = helpers.deliver(
        am_fixture("resolved_group"), token, fakes.FailingEventStore(), LATER
    )

    processor.process_envelope(envelope.pk, store=store)
    issue = issue_models.Issue.objects.get()

    result = (issue.event_count, issue.open_episode_count, issue.source_state)
    expected = (2, 0, "resolved")

    assert result == expected


# out-of-order delivery


def test_a_resolution_that_overtakes_its_firing_still_settles(am_fixture, token, store):
    """Should end with one closed episode when the webhooks arrive backwards."""
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("firing_group"), token, store, LATER)
    issue = issue_models.Issue.objects.get()

    result = (
        issue.event_count,
        issue.open_episode_count,
        issue.source_state,
        issue_models.Episode.objects.count(),
    )
    expected = (2, 2, "firing", 2)

    assert result == expected


def test_delivering_the_same_group_four_times_settles_on_two_deliveries_each(
    am_fixture, token, store
):
    """Should converge no matter how many times the inbox is drained."""
    for _ in range(2):
        helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
        helpers.deliver(am_fixture("repeat_delivery"), token, store, LATER)

    issue = issue_models.Issue.objects.get()

    result = (
        issue.event_count,
        issue.open_episode_count,
        sorted(
            episode.delivery_count for episode in issue_models.Episode.objects.all()
        ),
    )
    expected = (2, 2, [4, 4])

    assert result == expected
