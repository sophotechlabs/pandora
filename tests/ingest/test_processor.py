import copy
import datetime

import pytest

from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.issues import models as issue_models
from tests.ingest import fakes, helpers

RECEIVED_AT = datetime.datetime(2026, 8, 4, 9, 15, tzinfo=datetime.UTC)
FIRST_STARTED_AT = datetime.datetime(2026, 8, 4, 9, 12, 41, 123000, tzinfo=datetime.UTC)
RESOLVED_AT = datetime.datetime(2026, 8, 4, 9, 47, 41, 123000, tzinfo=datetime.UTC)
LATER = datetime.datetime(2026, 8, 4, 11, 35, tzinfo=datetime.UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def store():
    return fakes.RecordingEventStore()


@pytest.fixture
def firing(am_fixture, token, store):
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    return issue_models.Issue.objects.get()


# envelope handling


def test_a_processed_envelope_is_marked_done(am_fixture, token, store):
    """Should close the inbox row once the whole group is applied."""
    envelope = helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)

    result = (envelope.state, envelope.error)
    expected = (ingest_models.EnvelopeState.DONE, "")

    assert result == expected


def test_a_missing_envelope_is_logged_not_raised(caplog):
    """Should survive a queue message for a row the prune command already took."""
    with caplog.at_level("WARNING"):
        processor.process_envelope(4242, store=fakes.RecordingEventStore())

    assert "4242 is gone" in caplog.text


def test_a_finished_envelope_is_never_applied_twice(am_fixture, token, store):
    """Should make redelivery of the same queue message a no-op."""
    envelope = helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)

    processor.process_envelope(envelope.pk, store=store)

    result = issue_models.Issue.objects.get().event_count
    expected = 2

    assert result == expected


def test_a_failed_envelope_stays_replayable(am_fixture, token):
    """Should let a retry pick up an envelope that failed on a broken store."""
    envelope = helpers.deliver(
        am_fixture("firing_group"), token, fakes.FailingEventStore(), RECEIVED_AT
    )

    processor.process_envelope(envelope.pk, store=fakes.RecordingEventStore())
    envelope.refresh_from_db()

    result = (envelope.state, issue_models.Issue.objects.count())
    expected = (ingest_models.EnvelopeState.DONE, 1)

    assert result == expected


def test_the_consumer_falls_back_to_the_vendor_store(am_fixture, token):
    """Should reach for the configured store when the caller injects none."""
    envelope = helpers.store_envelope(am_fixture("firing_group"), token, RECEIVED_AT)

    processor.process_envelope(envelope.pk)
    envelope.refresh_from_db()

    result = envelope.state
    expected = ingest_models.EnvelopeState.PENDING

    assert result != expected


# failure path


def test_a_broken_payload_fails_the_envelope_with_the_reason(token, store):
    """Should keep the payload and say why it could not be translated."""
    envelope = helpers.deliver({"version": "5", "alerts": []}, token, store)

    result = (envelope.state, envelope.error)
    expected = (
        ingest_models.EnvelopeState.FAILED,
        "PayloadError: unsupported Alertmanager payload version '5'",
    )

    assert result == expected


def test_a_broken_payload_writes_no_issue(token, store):
    """Should leave the issue tables untouched when translation fails."""
    helpers.deliver({"version": "5", "alerts": []}, token, store)

    result = issue_models.Issue.objects.count()
    expected = 0

    assert result == expected


def test_a_store_failure_rolls_the_whole_group_back(am_fixture, token):
    """Should write nothing relational when the event store rejects the batch."""
    envelope = helpers.deliver(
        am_fixture("firing_group"), token, fakes.FailingEventStore(), RECEIVED_AT
    )

    result = (
        envelope.state,
        issue_models.Issue.objects.count(),
        issue_models.Episode.objects.count(),
    )
    expected = (ingest_models.EnvelopeState.FAILED, 0, 0)

    assert result == expected


def test_a_failure_is_logged_with_the_envelope(am_fixture, token, caplog):
    """Should name the envelope so an operator can replay exactly that row."""
    with caplog.at_level("ERROR"):
        envelope = helpers.deliver(
            am_fixture("firing_group"), token, fakes.FailingEventStore(), RECEIVED_AT
        )

    assert f"envelope {envelope.pk} failed" in caplog.text


# first delivery


def test_a_firing_group_opens_one_issue_for_both_pods(firing):
    """Should collapse two crash-looping pods into a single issue."""
    result = (
        firing.title,
        firing.level,
        firing.environment,
        firing.event_count,
        firing.open_episode_count,
        firing.source_state,
        firing.triage_state,
    )
    expected = (
        "KubePodCrashLooping: Pod is crash looping.",
        "error",
        "p-mk1",
        2,
        2,
        "firing",
        "new",
    )

    assert result == expected


def test_a_firing_group_dates_the_issue_from_the_first_alert(firing):
    """Should open the window at the earliest alert start, not the delivery."""
    result = (firing.first_seen, firing.last_seen)
    expected = (FIRST_STARTED_AT, RECEIVED_AT)

    assert result == expected


def test_a_firing_group_opens_one_episode_per_alert(firing):
    """Should keep per-pod history even though the pods share an issue."""
    result = sorted(
        (episode.am_fingerprint, episode.ends_at, episode.delivery_count)
        for episode in firing.episodes.all()
    )
    expected = [
        ("3c1f6a2b9d4e5087", None, 1),
        ("8b70e5d41c93a2f6", None, 1),
    ]

    assert result == expected


def test_an_episode_keeps_the_labels_it_was_grouped_from(firing):
    """Should store the full label set so regroup can replay it exactly."""
    episode = firing.episodes.get(am_fingerprint="3c1f6a2b9d4e5087")

    result = episode.labels["pod"]
    expected = "ledger-7d9f4c8b6d-hk2mp"

    assert result == expected


def test_the_creation_is_recorded_once(firing):
    """Should write one creation activity for the group, not one per alert."""
    result = [activity.kind for activity in firing.activities.all()]
    expected = ["created"]

    assert result == expected


def test_both_episodes_land_in_one_hour_bucket(firing):
    """Should bucket the sparkline by the hour the episodes started."""
    result = [(stat.hour, stat.count) for stat in firing.hourly_stats.all()]
    expected = [(datetime.datetime(2026, 8, 4, 9, tzinfo=datetime.UTC), 2)]

    assert result == expected


def test_the_pod_label_splits_into_two_tag_values(firing):
    """Should keep the distribution of the labels grouping threw away."""
    result = sorted(
        (stat.value, stat.count) for stat in firing.tag_stats.filter(key="pod")
    )
    expected = [
        ("ledger-7d9f4c8b6d-hk2mp", 1),
        ("ledger-7d9f4c8b6d-x4rtq", 1),
    ]

    assert result == expected


def test_the_shared_labels_count_once_per_alert(firing):
    """Should count a label common to both alerts twice, once per occurrence."""
    result = firing.tag_stats.get(key="namespace", value="payments").count
    expected = 2

    assert result == expected


# events


def test_one_event_row_is_written_per_alert(am_fixture, token, store):
    """Should hand the event store one blob per occurrence."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)

    result = len(store.rows)
    expected = 2

    assert result == expected


def test_an_event_carries_its_issue_and_episode(am_fixture, token, store):
    """Should link the blob to the rows that own its lifecycle state."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    issue = issue_models.Issue.objects.get()
    episodes = {str(episode.pk) for episode in issue.episodes.all()}

    result = (
        {row.issue_id for row in store.rows},
        {row.episode_id for row in store.rows} == episodes,
    )
    expected = ({issue.pk}, True)

    assert result == expected


def test_an_event_carries_the_translated_payload(am_fixture, token, store):
    """Should store the message, tags and level the translator derived."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    row = min(store.rows, key=lambda event: event.tags["pod"])

    result = (row.level, row.source, row.environment, row.timestamp, row.tags["pod"])
    expected = ("error", "am", "p-mk1", RECEIVED_AT, "ledger-7d9f4c8b6d-hk2mp")

    assert result == expected


def test_the_event_batch_is_written_once_per_envelope(am_fixture, token):
    """Should insert the whole group in one call, not one call per alert."""
    calls = []

    class CountingStore(fakes.RecordingEventStore):
        def insert(self, events):
            calls.append(len(events))
            super().insert(events)

    helpers.deliver(am_fixture("firing_group"), token, CountingStore(), RECEIVED_AT)

    result = calls
    expected = [2]

    assert result == expected


# resolution


def test_a_resolved_group_closes_every_episode(am_fixture, token, store, firing):
    """Should end both episodes and take the issue out of firing."""
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)
    firing.refresh_from_db()

    result = (
        firing.open_episode_count,
        firing.source_state,
        firing.event_count,
        sorted(episode.ends_at is None for episode in firing.episodes.all()),
    )
    expected = (0, "resolved", 2, [False, False])

    assert result == expected


def test_a_resolution_stamps_the_end_from_the_payload(am_fixture, token, store, firing):
    """Should close the episode at Alertmanager's end time."""
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)

    result = firing.episodes.get(am_fingerprint="3c1f6a2b9d4e5087").ends_at
    expected = RESOLVED_AT

    assert result == expected


def test_a_resolution_bumps_the_delivery_counters(am_fixture, token, store, firing):
    """Should count the resolution as another delivery of the same episode."""
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)

    result = firing.episodes.get(am_fingerprint="3c1f6a2b9d4e5087").delivery_count
    expected = 2

    assert result == expected


def test_one_pod_resolving_leaves_the_issue_firing(am_fixture, token, store, firing):
    """Should keep the issue live while the second pod is still crash looping."""
    helpers.deliver(am_fixture("mixed_group"), token, store, RECEIVED_AT)
    firing.refresh_from_db()

    result = (firing.open_episode_count, firing.source_state)
    expected = (1, "firing")

    assert result == expected


def test_a_resolution_never_counts_a_new_occurrence(am_fixture, token, store, firing):
    """Should leave the aggregates alone — only a new episode counts."""
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)
    firing.refresh_from_db()

    result = [(stat.hour, stat.count) for stat in firing.hourly_stats.all()]
    expected = [(datetime.datetime(2026, 8, 4, 9, tzinfo=datetime.UTC), 2)]

    assert result == expected


def test_a_resolution_for_an_unseen_alert_records_a_closed_episode(
    am_fixture, token, store
):
    """Should backfill history for an episode whose firing webhook never arrived."""
    helpers.deliver(am_fixture("resolved_unknown"), token, store, RECEIVED_AT)
    issue = issue_models.Issue.objects.get()

    result = (
        issue.event_count,
        issue.open_episode_count,
        issue.source_state,
        issue.episodes.get().ends_at.isoformat(),
    )
    expected = (1, 0, "resolved", "2026-08-04T01:35:00+00:00")

    assert result == expected


# regression


def refired(payload):
    later = copy.deepcopy(payload)
    for alert in later["alerts"]:
        alert["startsAt"] = "2026-08-04T11:30:00.000Z"
    return later


@pytest.fixture
def triaged(am_fixture, token, store, firing):
    helpers.deliver(am_fixture("resolved_group"), token, store, RECEIVED_AT)
    issue_models.Issue.objects.update(
        triage_state=issue_models.TriageState.RESOLVED,
        last_resolved_at=RECEIVED_AT,
    )
    return issue_models.Issue.objects.get()


def test_a_new_episode_reopens_a_resolved_issue(am_fixture, token, store, triaged):
    """Should pull an issue back onto the board when the alert returns."""
    helpers.deliver(refired(am_fixture("firing_group")), token, store, LATER)
    triaged.refresh_from_db()

    result = (triaged.triage_state, triaged.source_state, triaged.open_episode_count)
    expected = ("new", "firing", 2)

    assert result == expected


def test_a_regression_is_recorded(am_fixture, token, store, triaged):
    """Should say in the activity feed that this issue came back."""
    helpers.deliver(refired(am_fixture("firing_group")), token, store, LATER)

    result = [
        (activity.kind, activity.data)
        for activity in triaged.activities.filter(kind="regression")
    ]
    expected = [("regression", {"previous_triage_state": "resolved"})]

    assert result == expected


def test_a_regression_counts_the_new_episodes(am_fixture, token, store, triaged):
    """Should count the return as new occurrences, not as deliveries."""
    helpers.deliver(refired(am_fixture("firing_group")), token, store, LATER)
    triaged.refresh_from_db()

    result = (triaged.event_count, triaged.episodes.count())
    expected = (4, 4)

    assert result == expected


def test_a_repeat_of_the_old_episode_never_reopens_the_issue(
    am_fixture, token, store, triaged
):
    """Should leave a triaged issue alone when Alertmanager resends old history."""
    helpers.deliver(am_fixture("resolved_group"), token, store, LATER)
    triaged.refresh_from_db()

    result = (triaged.triage_state, triaged.source_state)
    expected = ("resolved", "resolved")

    assert result == expected


def test_replaying_an_old_envelope_never_reopens_a_resolved_issue(
    am_fixture, token, store, triaged
):
    """Should not undo a triage decision when an old envelope is replayed."""
    envelope = helpers.store_envelope(am_fixture("firing_group"), token, RECEIVED_AT)
    processor.process_envelope(envelope.pk, store=store)
    envelope.state = ingest_models.EnvelopeState.PENDING
    envelope.save(update_fields=["state"])
    processor.process_envelope(envelope.pk, store=store)
    triaged.refresh_from_db()

    result = triaged.triage_state
    expected = "resolved"

    assert result == expected


def test_a_late_renotification_of_a_still_firing_alert_regresses_it(
    am_fixture, token, store, triaged
):
    """Should notice a resolved issue is still firing, even with the original startsAt.

    Prometheus re-notifies with the alert's own start time, which predates the
    operator's triage. Gating on that start time hid a live alert from the board.
    """
    helpers.deliver(am_fixture("firing_group"), token, store, LATER)
    triaged.refresh_from_db()

    result = (triaged.triage_state, triaged.source_state)
    expected = ("new", "firing")

    assert result == expected


# regrouping-relevant identity


def test_two_alertnames_open_two_issues(am_fixture, token, store):
    """Should keep unrelated alerts in separate issues."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    helpers.deliver(am_fixture("truncated"), token, store, RECEIVED_AT)

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


def test_an_issue_belongs_to_the_project_that_owns_the_token(firing, token):
    """Should file every occurrence under the token's project."""
    result = firing.project_id
    expected = token.project_id

    assert result == expected


# one bad alert must not take its group down


def bad_sibling(payload):
    other = copy.deepcopy(payload)
    other["alerts"].insert(0, {"status": "firing", "fingerprint": ""})
    return other


def test_a_bad_alert_does_not_discard_its_siblings(am_fixture, token, store):
    """Should record the good alerts — one bad sibling used to drop the whole POST."""
    helpers.deliver(bad_sibling(am_fixture("firing_group")), token, store, RECEIVED_AT)

    result = issue_models.Episode.objects.count()
    expected = 2

    assert result == expected


def test_the_envelope_still_finishes_when_one_alert_is_unusable(
    am_fixture, token, store
):
    """Should not fail a whole envelope over one alert nothing can parse."""
    envelope = helpers.deliver(
        bad_sibling(am_fixture("firing_group")), token, store, RECEIVED_AT
    )

    result = envelope.state
    expected = ingest_models.EnvelopeState.DONE

    assert result == expected


def test_the_rejected_alert_is_recorded_on_the_envelope(am_fixture, token, store):
    """Should say what was dropped, so it is not silent."""
    envelope = helpers.deliver(
        bad_sibling(am_fixture("firing_group")), token, store, RECEIVED_AT
    )

    result = envelope.error
    expected = "alert 0: alert carries no fingerprint"

    assert result == expected


# two environments in one project


def other_environment(payload):
    other = copy.deepcopy(payload)
    for alert in other["alerts"]:
        alert["labels"]["cluster"] = "p-mk1"
    return other


def test_two_environments_are_one_issue(am_fixture, token, store):
    """Should be one issue with one resolution boundary, whichever cluster it fired on."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    envelope = helpers.store_envelope(am_fixture("firing_group"), token, RECEIVED_AT)
    ingest_models.RawEnvelope.objects.filter(pk=envelope.pk).update(environment="p-mk2")
    processor.process_envelope(envelope.pk, store=store)

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_both_environments_are_recorded_on_the_issue(am_fixture, token, store):
    """Should still say where it fired — the environment moved from the key to a row."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)
    envelope = helpers.store_envelope(am_fixture("firing_group"), token, RECEIVED_AT)
    ingest_models.RawEnvelope.objects.filter(pk=envelope.pk).update(environment="p-mk2")
    processor.process_envelope(envelope.pk, store=store)

    result = sorted(
        issue_models.IssueEnvironment.objects.values_list("name", flat=True)
    )
    expected = ["p-mk1", "p-mk2"]

    assert result == expected


def test_an_alertmanager_issue_records_the_rule_that_grouped_it(
    am_fixture, token, store
):
    """Should let a wrongly-grouped issue point at the rule to change."""
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)

    issue = issue_models.Issue.objects.get()
    result = (issue.grouping_source, issue.grouping_rule_id is not None)
    expected = (issue_models.GroupingSource.RULE, True)

    assert result == expected


def test_an_issue_grouped_by_the_built_in_denylist_says_so(
    am_fixture, token, store, project
):
    """Should distinguish the seeded rule from no rule at all."""
    issue_models.GroupingRule.objects.all().delete()
    helpers.deliver(am_fixture("firing_group"), token, store, RECEIVED_AT)

    result = issue_models.Issue.objects.get().grouping_source
    expected = issue_models.GroupingSource.DEFAULT

    assert result == expected
