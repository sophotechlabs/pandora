import dataclasses
import datetime
import inspect

import pytest

from pandora.issues import models, triage

AT = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)


# purity contract


def test_the_triage_rules_touch_no_orm():
    """Should stay callable from a queue worker that never opens the admin."""
    source = inspect.getsource(triage)

    assert "django" not in source


def test_a_plan_is_immutable():
    """Should describe writes, not hold state a caller can edit."""
    assert triage.TriagePlan.__dataclass_params__.frozen is True


def test_a_plan_describes_every_write_the_caller_makes():
    """Should name the fields and the audit record, nothing more."""
    result = [field.name for field in dataclasses.fields(triage.TriagePlan)]
    expected = ["changed", "issue_fields", "activity_kind"]

    assert result == expected


def test_a_bare_plan_writes_nothing():
    """Should let a caller treat 'no change' as the default outcome."""
    result = dataclasses.asdict(triage.TriagePlan())
    expected = {"changed": False, "issue_fields": {}, "activity_kind": ""}

    assert result == expected


# state name contract


def test_the_state_names_match_the_frozen_model_choices():
    """Should keep the pure strings equal to what the Issue column stores."""
    result = [triage.NEW, triage.ACKNOWLEDGED, triage.RESOLVED, triage.IGNORED]
    expected = [
        models.TriageState.NEW.value,
        models.TriageState.ACKNOWLEDGED.value,
        models.TriageState.RESOLVED.value,
        models.TriageState.IGNORED.value,
    ]

    assert result == expected


def test_open_means_untriaged_or_acknowledged():
    """Should define the default changelist view as work still to be done."""
    result = triage.OPEN_STATES
    expected = (models.TriageState.NEW.value, models.TriageState.ACKNOWLEDGED.value)

    assert result == expected


def test_only_the_three_human_targets_are_offered():
    """Should keep 'new' out of the action set — ingest owns that state."""
    result = triage.TARGET_STATES
    expected = (triage.ACKNOWLEDGED, triage.RESOLVED, triage.IGNORED)

    assert result == expected


def test_every_activity_kind_is_one_the_model_stores():
    """Should never write an audit row the admin cannot label."""
    kinds = {*triage.ACTIVITY_FOR_TARGET.values(), triage.REOPENED_ACTIVITY}

    result = sorted(kinds - set(models.ActivityKind.values))
    expected = []

    assert result == expected


# transitions


@pytest.mark.parametrize("state", [triage.NEW, triage.ACKNOWLEDGED, triage.IGNORED])
def test_moving_to_a_new_state_writes_the_state(state):
    """Should set the target state whenever it differs from the current one."""
    plan = triage.plan_triage(state, triage.RESOLVED, AT)

    result = plan.issue_fields["triage_state"]
    expected = triage.RESOLVED

    assert plan.changed is True
    assert result == expected


def test_resolving_stamps_the_resolution_time():
    """Should record when the issue was closed so a regression can be dated."""
    plan = triage.plan_triage(triage.NEW, triage.RESOLVED, AT)

    result = plan.issue_fields
    expected = {"triage_state": triage.RESOLVED, "last_resolved_at": AT}

    assert result == expected


def test_acknowledging_leaves_the_resolution_time_alone():
    """Should touch only the state when the issue is not being closed."""
    plan = triage.plan_triage(triage.NEW, triage.ACKNOWLEDGED, AT)

    result = plan.issue_fields
    expected = {"triage_state": triage.ACKNOWLEDGED}

    assert result == expected


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        (triage.ACKNOWLEDGED, "acknowledged"),
        (triage.RESOLVED, "resolved"),
        (triage.IGNORED, "ignored"),
    ],
)
def test_each_target_has_its_own_activity_kind(target, kind):
    """Should log what a reader of the feed expects to see."""
    plan = triage.plan_triage(triage.NEW, target, AT)

    result = plan.activity_kind
    expected = kind

    assert result == expected


@pytest.mark.parametrize("target", [triage.ACKNOWLEDGED, triage.IGNORED])
def test_moving_off_resolved_logs_a_reopen(target):
    """Should mark a hand-reopened issue distinctly from a fresh acknowledge."""
    plan = triage.plan_triage(triage.RESOLVED, target, AT)

    result = plan.activity_kind
    expected = triage.REOPENED_ACTIVITY

    assert result == expected


@pytest.mark.parametrize("state", triage.TARGET_STATES)
def test_a_state_that_is_already_set_writes_nothing(state):
    """Should make a repeated bulk action a no-op instead of an audit spam."""
    plan = triage.plan_triage(state, state, AT)

    result = dataclasses.asdict(plan)
    expected = {"changed": False, "issue_fields": {}, "activity_kind": ""}

    assert result == expected
