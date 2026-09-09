import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from pandora.issues import escalation, models

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 9, 7, 12, 30, tzinfo=datetime.UTC)
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def quiet(project):
    def build(**overrides):
        fields = {
            "project": project,
            "fingerprint_hash": "a" * 64,
            "title": "ValueError: bad input",
            "level": models.Level.ERROR,
            "first_seen": NOW - datetime.timedelta(days=20),
            "last_seen": NOW,
            "event_count": 400,
            "triage_state": models.TriageState.IGNORED,
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build


def history(issue, counts, recent, at=NOW):
    hour = at.replace(minute=0, second=0, microsecond=0)
    for index, count in enumerate(counts):
        models.HourlyStat.objects.create(
            issue=issue,
            hour=hour - datetime.timedelta(hours=index + 1),
            count=count,
        )
    models.HourlyStat.objects.create(issue=issue, hour=hour, count=recent)


def test_a_quiet_issue_that_stayed_quiet_is_left_alone(quiet):
    """Should not wake an ignored issue that is doing what it always did."""
    issue = quiet()
    history(issue, [4, 5, 6], recent=5)

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.escalated, issue.triage_state)
    expected = ([], models.TriageState.IGNORED)

    assert result == expected


def test_a_quiet_issue_that_came_back_loud_escalates(quiet):
    """Should be the reason an archived issue is worth archiving at all."""
    issue = quiet()
    history(issue, [1, 2, 1], recent=60)

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.escalated, issue.triage_state, issue.needs_review)
    expected = ([issue.pk], models.TriageState.NEW, True)

    assert result == expected


def test_an_escalation_writes_the_trail(quiet):
    """Should say why the issue came back, and how loud it got."""
    issue = quiet()
    history(issue, [1, 1, 1], recent=60)

    escalation.run(NOW)

    activity = models.IssueActivity.objects.get(kind=models.ActivityKind.ESCALATED)
    result = (activity.data["previous_triage_state"], activity.data["count"])
    expected = ("ignored", 60)

    assert result == expected


def test_an_escalation_raises_the_priority(quiet):
    """Should put the issue at the top of the queue it just re-entered."""
    issue = quiet(priority=models.Priority.LOW)
    history(issue, [1, 1, 1], recent=60)

    escalation.run(NOW)

    issue.refresh_from_db()
    assert issue.priority == models.Priority.HIGH


def test_an_escalation_leaves_a_pinned_priority_alone(quiet):
    """Should respect a rank a person set by hand, the way the sweep does."""
    issue = quiet(priority=models.Priority.LOW, priority_locked=True)
    history(issue, [1, 1, 1], recent=60)

    escalation.run(NOW)

    issue.refresh_from_db()
    assert issue.priority == models.Priority.LOW


def test_an_escalation_clears_the_snooze(quiet):
    """Should not leave a woken issue still muted by its old snooze."""
    issue = quiet(
        triage_state=models.TriageState.NEW,
        snoozed_until=NOW + datetime.timedelta(days=1),
    )
    history(issue, [1, 1, 1], recent=60)

    escalation.run(NOW)

    issue.refresh_from_db()
    result = (issue.snoozed_until, issue.snoozed_past_count)
    expected = (None, None)

    assert result == expected


def test_a_burst_under_the_floor_does_not_escalate(quiet, settings):
    """Should not wake an issue on three events, whatever the ratio says."""
    settings.PANDORA_ESCALATION_FLOOR = 10
    issue = quiet()
    history(issue, [0, 0, 0], recent=3)

    escalation.run(NOW)

    issue.refresh_from_db()
    assert issue.triage_state == models.TriageState.IGNORED


def test_an_open_issue_is_not_escalated(quiet):
    """Should leave an issue somebody is already looking at where it is."""
    issue = quiet(triage_state=models.TriageState.ACKNOWLEDGED)
    history(issue, [1, 1, 1], recent=60)

    report = escalation.run(NOW)

    assert report.escalated == []


def test_the_baseline_is_the_median_not_the_mean():
    """Should not let one earlier spike raise the bar over the next one."""
    result = escalation.rate_baseline([1, 1, 1, 1, 500])
    expected = 1.0

    assert result == expected


def test_an_issue_with_no_history_escalates_on_the_floor(quiet):
    """Should treat a first burst on a muted issue as an escalation."""
    issue = quiet()
    models.HourlyStat.objects.create(issue=issue, hour=HOUR, count=40)

    report = escalation.run(NOW)

    assert report.escalated == [issue.pk]


def test_a_stale_issue_auto_resolves_when_the_project_says_so(quiet, project):
    """Should clear a board nobody will ever come back to by hand."""
    project.auto_resolve_days = 7
    project.save(update_fields=["auto_resolve_days"])
    issue = quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
    )

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.auto_resolved, issue.triage_state, issue.needs_review)
    expected = ([issue.pk], models.TriageState.RESOLVED, False)

    assert result == expected


def test_auto_resolve_is_off_until_a_window_is_set(quiet):
    """Should never resolve anything an operator did not ask to be resolved."""
    issue = quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=300),
    )

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.auto_resolved, issue.triage_state)
    expected = ([], models.TriageState.NEW)

    assert result == expected


def test_the_global_window_applies_to_a_project_with_none(quiet, settings):
    """Should let one environment variable set the policy for every project."""
    settings.PANDORA_AUTO_RESOLVE_DAYS = 7
    issue = quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
    )

    report = escalation.run(NOW)

    assert report.auto_resolved == [issue.pk]


def test_a_project_window_overrides_the_global_one(quiet, project, settings):
    """Should let one noisy project keep a longer window than the rest."""
    settings.PANDORA_AUTO_RESOLVE_DAYS = 7
    project.auto_resolve_days = 90
    project.save(update_fields=["auto_resolve_days"])
    quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
    )

    report = escalation.run(NOW)

    assert report.auto_resolved == []


def test_an_issue_with_a_firing_alert_is_never_auto_resolved(quiet, settings):
    """Should not close an issue whose alert is still open in Alertmanager."""
    settings.PANDORA_AUTO_RESOLVE_DAYS = 7
    quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
        open_episode_count=1,
    )

    report = escalation.run(NOW)

    assert report.auto_resolved == []


def test_a_snoozed_issue_is_not_auto_resolved(quiet, settings):
    """Should leave an issue somebody deliberately parked until it wakes."""
    settings.PANDORA_AUTO_RESOLVE_DAYS = 7
    quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
        snoozed_until=NOW + datetime.timedelta(days=5),
    )

    report = escalation.run(NOW)

    assert report.auto_resolved == []


def test_an_auto_resolve_writes_the_trail(quiet, settings):
    """Should distinguish an age-based close from one a person made."""
    settings.PANDORA_AUTO_RESOLVE_DAYS = 7
    quiet(
        triage_state=models.TriageState.NEW,
        last_seen=NOW - datetime.timedelta(days=30),
    )

    escalation.run(NOW)

    activity = models.IssueActivity.objects.get(kind=models.ActivityKind.AUTO_RESOLVED)
    assert activity.data["days"] == 7


def test_the_sweep_reranks_an_issue_whose_reach_grew(quiet, settings):
    """Should raise a fault that has spread since the last sweep."""
    settings.PANDORA_PRIORITY_USER_THRESHOLD = 100
    issue = quiet(priority=models.Priority.LOW, user_count=400)

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.reprioritised, issue.priority)
    expected = ([issue.pk], models.Priority.HIGH)

    assert result == expected


def test_the_sweep_leaves_a_pinned_rank_alone(quiet):
    """Should stop overwriting a decision a person already made."""
    issue = quiet(priority=models.Priority.LOW, priority_locked=True, user_count=9999)

    report = escalation.run(NOW)

    issue.refresh_from_db()
    result = (report.reprioritised, issue.priority)
    expected = ([], models.Priority.LOW)

    assert result == expected


def test_the_command_reports_what_it_did(quiet, capsys):
    """Should print one line a cron log can be read from."""
    issue = quiet()
    history(issue, [1, 1, 1], recent=60, at=timezone.now())

    call_command("triage")

    assert "triage: escalated 1" in capsys.readouterr().out


def test_the_sweep_runs_against_the_wall_clock(quiet):
    """Should be callable with no arguments, which is how cron calls it."""
    quiet()

    call_command("triage")

    assert models.Issue.objects.filter(triage_state="ignored").exists()


def test_an_escalation_queues_a_notification(quiet, settings):
    """Should tell the destination the issue came back, which is the point."""
    from pandora.notify import models as notify_models

    settings.PANDORA_ESCALATION_HOOKS = "pandora.notify.hooks.on_escalate"
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.ESCALATING],
        min_level=models.Level.DEBUG,
    )
    issue = quiet()
    history(issue, [1, 1, 1], recent=60, at=timezone.now())

    escalation.run(timezone.now())

    delivery = notify_models.Delivery.objects.get()
    result = (delivery.event, delivery.payload["count"])
    expected = (notify_models.ESCALATING, 60)

    assert result == expected


def test_a_triage_action_ends_the_escalation(quiet):
    """Should let the rank fall back once a person has dealt with the issue."""
    from pandora.issues import actions

    issue = quiet()
    history(issue, [1, 1, 1], recent=60)
    escalation.run(NOW)

    issue.refresh_from_db()
    actions.apply_triage(issue, models.TriageState.ACKNOWLEDGED, "dev", NOW)
    escalation.run(NOW + datetime.timedelta(hours=2))

    issue.refresh_from_db()
    result = (issue.escalated_at, issue.priority)
    expected = (None, models.Priority.MEDIUM)

    assert result == expected


def test_an_escalated_issue_keeps_its_rank_across_sweeps(quiet):
    """Should not quietly demote an issue nobody has looked at yet."""
    issue = quiet()
    history(issue, [1, 1, 1], recent=60)
    escalation.run(NOW)

    escalation.run(NOW + datetime.timedelta(hours=2))

    issue.refresh_from_db()
    assert issue.priority == models.Priority.HIGH
