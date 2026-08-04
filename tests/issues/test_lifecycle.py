import dataclasses
import datetime
import inspect

import pytest

from pandora.issues import lifecycle

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


# behaviour, once Phase 1 lands


def test_apply_occurrence_is_the_seam_phase_one_fills():
    """Should raise NotImplementedError until Phase 1 implements the rules."""
    with pytest.raises(NotImplementedError):
        lifecycle.apply_occurrence(None, None, lifecycle.Occurrence(**OCCURRENCE))
