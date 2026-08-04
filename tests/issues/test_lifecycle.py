import dataclasses
import datetime
import inspect

import pytest

from pandora.issues import lifecycle, models

FIRED_AT = datetime.datetime(2026, 8, 4, 9, 12, tzinfo=datetime.UTC)
DELIVERED_AT = datetime.datetime(2026, 8, 4, 9, 13, tzinfo=datetime.UTC)
ENDED_AT = datetime.datetime(2026, 8, 4, 9, 47, tzinfo=datetime.UTC)

OCCURRENCE = {
    "fingerprint": ["alertname:TargetDown", "namespace:monitoring"],
    "fingerprint_hash": "a" * 64,
    "grouping_labels": {"alertname": "TargetDown", "namespace": "monitoring"},
    "am_fingerprint": "3c1f6a2b9d4e5087",
    "labels": {"alertname": "TargetDown", "job": "node-exporter"},
    "status": "firing",
    "title": "TargetDown: scrape target unreachable",
    "culprit": "alertname=TargetDown namespace=monitoring",
    "level": "warning",
    "message": "1 of 4 node-exporter targets is down",
    "starts_at": datetime.datetime(2026, 8, 4, 9, 12, tzinfo=datetime.UTC),
    "ends_at": None,
    "timestamp": datetime.datetime(2026, 8, 4, 9, 12, tzinfo=datetime.UTC),
}


def field_names(record):
    return [field.name for field in dataclasses.fields(record)]


def occurrence(**overrides):
    return lifecycle.Occurrence(
        **{**OCCURRENCE, "timestamp": DELIVERED_AT, **overrides}
    )


def issue_state(**overrides):
    base = {
        "triage_state": "new",
        "open_episode_count": 1,
        "level": "warning",
        "first_seen": FIRED_AT,
        "last_seen": FIRED_AT,
    }
    return lifecycle.IssueState(**{**base, **overrides})


def episode_state(**overrides):
    base = {"starts_at": FIRED_AT, "ends_at": None, "delivery_count": 1}
    return lifecycle.EpisodeState(**{**base, **overrides})


def transition_fields(transition):
    return {
        "create_issue": transition.create_issue,
        "create_episode": transition.create_episode,
        "close_episode": transition.close_episode,
        "bump_delivery": transition.bump_delivery,
        "count_occurrence": transition.count_occurrence,
        "open_episode_delta": transition.open_episode_delta,
    }


# signature contract


def test_apply_occurrence_takes_plain_data_and_returns_a_transition():
    """Should accept optional issue and episode snapshots plus one occurrence."""
    signature = inspect.signature(lifecycle.apply_occurrence)

    result = list(signature.parameters)
    expected = ["issue_state", "episode_state", "occurrence"]

    assert result == expected


def test_apply_occurrence_touches_no_orm():
    """Should keep the lifecycle module free of Django imports — pure functions."""
    source = inspect.getsource(lifecycle)

    assert "django" not in source


# dataclass contract


def test_occurrence_carries_what_a_translator_produces():
    """Should name every field the Alertmanager and SDK translators must fill."""
    result = field_names(lifecycle.Occurrence)
    expected = [
        "fingerprint",
        "fingerprint_hash",
        "grouping_labels",
        "am_fingerprint",
        "labels",
        "status",
        "title",
        "culprit",
        "level",
        "message",
        "starts_at",
        "ends_at",
        "timestamp",
        "tags",
        "extra",
        "environment",
        "source",
    ]

    assert result == expected


def test_issue_state_carries_only_what_a_transition_depends_on():
    """Should snapshot the issue fields the state machine reads, nothing else."""
    result = field_names(lifecycle.IssueState)
    expected = [
        "triage_state",
        "open_episode_count",
        "level",
        "first_seen",
        "last_seen",
        "last_resolved_at",
    ]

    assert result == expected


def test_episode_state_carries_only_the_episode_identity_and_counters():
    """Should snapshot the episode fields the state machine reads."""
    result = field_names(lifecycle.EpisodeState)
    expected = ["starts_at", "ends_at", "delivery_count"]

    assert result == expected


def test_transition_describes_every_write_the_consumer_makes():
    """Should name each write so the consumer never re-derives lifecycle rules."""
    result = field_names(lifecycle.Transition)
    expected = [
        "create_issue",
        "create_episode",
        "close_episode",
        "bump_delivery",
        "count_occurrence",
        "open_episode_delta",
        "issue_fields",
        "activities",
    ]

    assert result == expected


def test_a_transition_defaults_to_writing_nothing():
    """Should let a caller build a no-op transition and add only what applies."""
    result = dataclasses.asdict(lifecycle.Transition())
    expected = {
        "create_issue": False,
        "create_episode": False,
        "close_episode": False,
        "bump_delivery": False,
        "count_occurrence": False,
        "open_episode_delta": 0,
        "issue_fields": {},
        "activities": (),
    }

    assert result == expected


def test_an_occurrence_defaults_its_optional_payload():
    """Should default tags, extra, environment and source for a bare occurrence."""
    occurrence = lifecycle.Occurrence(**OCCURRENCE)

    result = {
        "tags": occurrence.tags,
        "extra": occurrence.extra,
        "environment": occurrence.environment,
        "source": occurrence.source,
    }
    expected = {"tags": {}, "extra": {}, "environment": "", "source": "am"}

    assert result == expected


@pytest.mark.parametrize(
    "record",
    [
        lifecycle.Occurrence,
        lifecycle.IssueState,
        lifecycle.EpisodeState,
        lifecycle.ActivityRecord,
        lifecycle.Transition,
    ],
)
def test_every_lifecycle_record_is_immutable(record):
    """Should freeze the lifecycle records — they describe writes, not state."""
    assert record.__dataclass_params__.frozen is True


# vocabulary contract


@pytest.mark.parametrize(
    ("constant", "choice"),
    [
        (lifecycle.SOURCE_FIRING, models.SourceState.FIRING),
        (lifecycle.SOURCE_RESOLVED, models.SourceState.RESOLVED),
        (lifecycle.TRIAGE_NEW, models.TriageState.NEW),
        (lifecycle.TRIAGE_RESOLVED, models.TriageState.RESOLVED),
        (lifecycle.ACTIVITY_CREATED, models.ActivityKind.CREATED),
        (lifecycle.ACTIVITY_REGRESSION, models.ActivityKind.REGRESSION),
    ],
)
def test_every_lifecycle_constant_is_a_value_the_schema_accepts(constant, choice):
    """Should spell states exactly as the models do — the module cannot import them."""
    assert constant == choice


def test_a_new_issue_starts_from_the_occurrence_with_empty_counters():
    """Should hand the consumer a complete row for a first-sighting insert."""
    result = lifecycle.new_issue_fields(occurrence())
    expected = {
        "title": OCCURRENCE["title"],
        "culprit": OCCURRENCE["culprit"],
        "level": "warning",
        "environment": "",
        "fingerprint": OCCURRENCE["fingerprint"],
        "grouping_labels": OCCURRENCE["grouping_labels"],
        "first_seen": FIRED_AT,
        "last_seen": DELIVERED_AT,
        "event_count": 0,
        "open_episode_count": 0,
        "source_state": None,
        "triage_state": "new",
    }

    assert result == expected


def test_the_new_issue_payload_copies_the_occurrence_collections():
    """Should copy fingerprint and labels so a frozen occurrence stays frozen."""
    built = occurrence()

    result = lifecycle.new_issue_fields(built)

    assert result["fingerprint"] is not built.fingerprint
    assert result["grouping_labels"] is not built.grouping_labels


# first sighting


def test_a_first_firing_alert_opens_an_issue_and_an_episode():
    """Should create both rows and count the occurrence once."""
    result = transition_fields(lifecycle.apply_occurrence(None, None, occurrence()))
    expected = {
        "create_issue": True,
        "create_episode": True,
        "close_episode": False,
        "bump_delivery": False,
        "count_occurrence": True,
        "open_episode_delta": 1,
    }

    assert result == expected


def test_a_first_firing_alert_marks_the_issue_firing():
    """Should derive source_state from the open episode count, never set it raw."""
    transition = lifecycle.apply_occurrence(None, None, occurrence())

    result = transition.issue_fields
    expected = {
        "last_seen": DELIVERED_AT,
        "source_state": "firing",
        "first_seen": FIRED_AT,
    }

    assert result == expected


def test_a_first_sighting_records_the_creation():
    """Should leave an audit trail the moment an issue appears."""
    transition = lifecycle.apply_occurrence(None, None, occurrence())

    result = [
        (record.kind, record.actor, record.data) for record in transition.activities
    ]
    expected = [("created", "", {})]

    assert result == expected


def test_a_resolution_for_an_unseen_alert_records_a_closed_episode():
    """Should record history pandora missed rather than dropping the alert."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    result = transition_fields(lifecycle.apply_occurrence(None, None, resolved))
    expected = {
        "create_issue": True,
        "create_episode": True,
        "close_episode": False,
        "bump_delivery": False,
        "count_occurrence": True,
        "open_episode_delta": 0,
    }

    assert result == expected


def test_an_issue_born_resolved_is_not_firing():
    """Should not show a firing dot for an episode that arrived already over."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    result = lifecycle.apply_occurrence(None, None, resolved).issue_fields[
        "source_state"
    ]
    expected = "resolved"

    assert result == expected


# repeat deliveries


def test_a_repeat_delivery_moves_counters_only():
    """Should bump delivery bookkeeping without counting a second occurrence."""
    result = transition_fields(
        lifecycle.apply_occurrence(issue_state(), episode_state(), occurrence())
    )
    expected = {
        "create_issue": False,
        "create_episode": False,
        "close_episode": False,
        "bump_delivery": True,
        "count_occurrence": False,
        "open_episode_delta": 0,
    }

    assert result == expected


def test_a_repeat_delivery_keeps_the_issue_firing():
    """Should leave an issue firing while its episode is still open."""
    transition = lifecycle.apply_occurrence(
        issue_state(), episode_state(), occurrence()
    )

    result = transition.issue_fields["source_state"]
    expected = "firing"

    assert result == expected


def test_a_repeated_resolution_closes_nothing_twice():
    """Should not decrement the open count on a resend of the same resolution."""
    closed = episode_state(ends_at=ENDED_AT)
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    result = transition_fields(
        lifecycle.apply_occurrence(issue_state(open_episode_count=0), closed, resolved)
    )
    expected = {
        "create_issue": False,
        "create_episode": False,
        "close_episode": False,
        "bump_delivery": True,
        "count_occurrence": False,
        "open_episode_delta": 0,
    }

    assert result == expected


# resolution


def test_a_resolution_closes_the_open_episode():
    """Should close the episode and give the issue back one open slot."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    result = transition_fields(
        lifecycle.apply_occurrence(issue_state(), episode_state(), resolved)
    )
    expected = {
        "create_issue": False,
        "create_episode": False,
        "close_episode": True,
        "bump_delivery": True,
        "count_occurrence": False,
        "open_episode_delta": -1,
    }

    assert result == expected


def test_the_last_open_episode_closing_resolves_the_issue():
    """Should flip source_state to resolved once nothing is left firing."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    transition = lifecycle.apply_occurrence(
        issue_state(open_episode_count=1), episode_state(), resolved
    )

    result = transition.issue_fields["source_state"]
    expected = "resolved"

    assert result == expected


def test_one_pod_resolving_leaves_the_issue_firing_while_another_burns():
    """Should keep the issue firing when a sibling episode is still open."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    transition = lifecycle.apply_occurrence(
        issue_state(open_episode_count=2), episode_state(), resolved
    )

    result = transition.issue_fields["source_state"]
    expected = "firing"

    assert result == expected


def test_an_impossible_negative_open_count_is_clamped():
    """Should never derive a firing state from a counter that drifted below zero."""
    resolved = occurrence(status="resolved", ends_at=ENDED_AT)

    transition = lifecycle.apply_occurrence(
        issue_state(open_episode_count=0), episode_state(), resolved
    )

    result = transition.issue_fields["source_state"]
    expected = "resolved"

    assert result == expected


# re-firing


def test_a_closed_episode_that_fires_again_reopens():
    """Should reopen an episode Alertmanager re-fires under the same start."""
    result = transition_fields(
        lifecycle.apply_occurrence(
            issue_state(open_episode_count=0),
            episode_state(ends_at=ENDED_AT),
            occurrence(),
        )
    )
    expected = {
        "create_issue": False,
        "create_episode": False,
        "close_episode": False,
        "bump_delivery": True,
        "count_occurrence": False,
        "open_episode_delta": 1,
    }

    assert result == expected


# regression


def test_a_new_episode_on_a_resolved_issue_regresses_it():
    """Should drag a triaged-away issue back to new when it fires again."""
    transition = lifecycle.apply_occurrence(
        issue_state(triage_state="resolved", open_episode_count=0), None, occurrence()
    )

    result = transition.issue_fields["triage_state"]
    expected = "new"

    assert result == expected


def test_a_regression_is_recorded_with_the_state_it_came_from():
    """Should say what the issue was before it came back."""
    transition = lifecycle.apply_occurrence(
        issue_state(triage_state="resolved", open_episode_count=0), None, occurrence()
    )

    result = [(record.kind, record.data) for record in transition.activities]
    expected = [("regression", {"previous_triage_state": "resolved"})]

    assert result == expected


def test_a_repeat_delivery_never_regresses_a_resolved_issue():
    """Should keep a triaged issue closed while the same episode keeps arriving."""
    transition = lifecycle.apply_occurrence(
        issue_state(triage_state="resolved"), episode_state(), occurrence()
    )

    result = transition.issue_fields.get("triage_state")
    expected = None

    assert result == expected
    assert transition.activities == ()


def test_an_episode_that_started_before_the_resolution_does_not_regress():
    """Should ignore a replayed old episode instead of undoing a triage decision."""
    state = issue_state(
        triage_state="resolved",
        open_episode_count=0,
        last_resolved_at=FIRED_AT + datetime.timedelta(hours=1),
    )

    transition = lifecycle.apply_occurrence(state, None, occurrence())

    result = transition.issue_fields.get("triage_state")
    expected = None

    assert result == expected


def test_an_episode_that_started_after_the_resolution_regresses():
    """Should regress on a genuinely new episode after the issue was resolved."""
    state = issue_state(
        triage_state="resolved",
        open_episode_count=0,
        last_resolved_at=FIRED_AT - datetime.timedelta(hours=1),
    )

    transition = lifecycle.apply_occurrence(state, None, occurrence())

    result = transition.issue_fields["triage_state"]
    expected = "new"

    assert result == expected


@pytest.mark.parametrize("triage", ["new", "ack", "ignored"])
def test_only_a_resolved_issue_regresses(triage):
    """Should leave acknowledged and ignored issues where the operator put them."""
    transition = lifecycle.apply_occurrence(
        issue_state(triage_state=triage, open_episode_count=0), None, occurrence()
    )

    result = transition.issue_fields.get("triage_state")
    expected = None

    assert result == expected


# timestamps


def test_last_seen_moves_forward_with_the_delivery():
    """Should treat the delivery clock as when the issue was last heard from."""
    transition = lifecycle.apply_occurrence(
        issue_state(), episode_state(), occurrence()
    )

    result = transition.issue_fields["last_seen"]
    expected = DELIVERED_AT

    assert result == expected


def test_last_seen_never_walks_backwards_on_a_replayed_envelope():
    """Should keep the newest sighting when an older envelope is replayed."""
    later = DELIVERED_AT + datetime.timedelta(hours=3)

    transition = lifecycle.apply_occurrence(
        issue_state(last_seen=later), episode_state(), occurrence()
    )

    result = transition.issue_fields["last_seen"]
    expected = later

    assert result == expected


def test_first_seen_moves_earlier_when_an_older_episode_arrives():
    """Should stretch the issue window back for a late-arriving old alert."""
    older = FIRED_AT - datetime.timedelta(days=1)

    transition = lifecycle.apply_occurrence(
        issue_state(), None, occurrence(starts_at=older)
    )

    result = transition.issue_fields["first_seen"]
    expected = older

    assert result == expected


def test_first_seen_stays_put_for_a_newer_episode():
    """Should leave the issue's first sighting alone when a newer episode opens."""
    transition = lifecycle.apply_occurrence(
        issue_state(first_seen=FIRED_AT - datetime.timedelta(days=1)),
        None,
        occurrence(),
    )

    result = transition.issue_fields.get("first_seen")
    expected = None

    assert result == expected


# the SDK door — events without episodes


def test_a_first_sdk_event_opens_an_issue_and_counts():
    """Should create the issue and count the event, with no episode involved."""
    transition = lifecycle.apply_event(None, occurrence(source="sdk"))

    result = (
        transition.create_issue,
        transition.count_occurrence,
        transition.create_episode,
        transition.close_episode,
        transition.open_episode_delta,
    )
    expected = (True, True, False, False, 0)

    assert result == expected


def test_an_sdk_event_never_touches_the_firing_column():
    """Should leave source_state alone — it is derived from episodes."""
    transition = lifecycle.apply_event(None, occurrence(source="sdk"))

    result = "source_state" in transition.issue_fields
    expected = False

    assert result == expected


def test_a_first_sdk_event_records_the_creation():
    """Should show the issue's birth in the activity feed."""
    transition = lifecycle.apply_event(None, occurrence(source="sdk"))

    result = [record.kind for record in transition.activities]
    expected = ["created"]

    assert result == expected


def test_a_repeat_sdk_event_creates_nothing():
    """Should count against the existing issue without recording an event."""
    transition = lifecycle.apply_event(issue_state(), occurrence(source="sdk"))

    result = (transition.create_issue, transition.activities)
    expected = (False, ())

    assert result == expected


def test_an_sdk_event_moves_last_seen_to_arrival():
    """Should track recency by when pandora saw it."""
    transition = lifecycle.apply_event(issue_state(), occurrence(source="sdk"))

    result = transition.issue_fields["last_seen"]
    expected = DELIVERED_AT

    assert result == expected


def test_a_late_sdk_event_does_not_rewind_last_seen():
    """Should keep the newest sighting when a delayed event arrives."""
    newer = DELIVERED_AT + datetime.timedelta(hours=1)
    transition = lifecycle.apply_event(
        issue_state(last_seen=newer), occurrence(source="sdk")
    )

    result = transition.issue_fields["last_seen"]
    expected = newer

    assert result == expected


def test_an_older_sdk_event_pulls_first_seen_back():
    """Should widen the window when an older event turns up."""
    older = FIRED_AT - datetime.timedelta(days=1)
    transition = lifecycle.apply_event(
        issue_state(), occurrence(source="sdk", starts_at=older)
    )

    result = transition.issue_fields["first_seen"]
    expected = older

    assert result == expected


def test_a_newer_sdk_event_leaves_first_seen_alone():
    """Should not move the issue's first sighting forward."""
    transition = lifecycle.apply_event(
        issue_state(first_seen=FIRED_AT - datetime.timedelta(days=1)),
        occurrence(source="sdk"),
    )

    result = transition.issue_fields.get("first_seen")
    expected = None

    assert result == expected


def test_an_sdk_event_on_a_resolved_issue_regresses_it():
    """Should reopen an issue that somebody resolved when it happens again."""
    transition = lifecycle.apply_event(
        issue_state(triage_state="resolved"), occurrence(source="sdk")
    )

    result = (
        transition.issue_fields["triage_state"],
        [record.kind for record in transition.activities],
    )
    expected = ("new", ["regression"])

    assert result == expected


def test_an_sdk_regression_records_the_state_it_came_from():
    """Should say what the issue was before it came back."""
    transition = lifecycle.apply_event(
        issue_state(triage_state="resolved"), occurrence(source="sdk")
    )

    result = transition.activities[0].data
    expected = {"previous_triage_state": "resolved"}

    assert result == expected


def test_an_sdk_event_before_the_resolution_does_not_regress():
    """Should ignore a straggler that predates the triage decision."""
    resolved_at = FIRED_AT + datetime.timedelta(hours=2)
    transition = lifecycle.apply_event(
        issue_state(triage_state="resolved", last_resolved_at=resolved_at),
        occurrence(source="sdk", starts_at=FIRED_AT),
    )

    result = transition.activities
    expected = ()

    assert result == expected


def test_an_sdk_event_after_the_resolution_regresses():
    """Should reopen when the event is newer than the resolution."""
    resolved_at = FIRED_AT - datetime.timedelta(hours=2)
    transition = lifecycle.apply_event(
        issue_state(triage_state="resolved", last_resolved_at=resolved_at),
        occurrence(source="sdk", starts_at=FIRED_AT),
    )

    result = [record.kind for record in transition.activities]
    expected = ["regression"]

    assert result == expected


def test_an_acknowledged_issue_is_not_regressed_by_an_sdk_event():
    """Should leave triage alone unless the issue was actually resolved."""
    transition = lifecycle.apply_event(
        issue_state(triage_state="ack"), occurrence(source="sdk")
    )

    result = (transition.activities, "triage_state" in transition.issue_fields)
    expected = ((), False)

    assert result == expected
