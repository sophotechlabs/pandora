import datetime

import pytest
from django.db import connection
from django.utils import timezone

from pandora.issues import merge
from pandora.issues import models as issue_models
from pandora.notify import models as notify_models
from pandora.people import models as people_models

pytestmark = pytest.mark.django_db

NOW = timezone.now()
FINGERPRINT = "f" * 64


# what the plan says


def test_a_single_issue_is_not_a_duplicate(make_twin):
    """Should leave an install that never split an issue completely alone."""
    make_twin("p-mk1")

    result = merge.plan().groups

    assert result == []


def test_two_environments_are_one_group(make_twin):
    """Should see the pair the old key created as one issue wearing two rows."""
    make_twin("p-mk1")
    make_twin("p-mk2")

    result = [group.environments for group in merge.plan().groups]
    expected = [["p-mk1", "p-mk2"]]

    assert result == expected


def test_the_oldest_row_is_the_keeper(make_twin):
    """Should keep the row whose first_seen is the issue's real beginning."""
    older = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=3))
    make_twin("p-mk2", first_seen=NOW - datetime.timedelta(hours=1))

    result = merge.plan().groups[0].keeper
    expected = older.pk

    assert result == expected


def test_a_dry_run_changes_nothing(make_twin):
    """Should let an operator see the fold before it happens."""
    make_twin("p-mk1")
    make_twin("p-mk2")

    merge.plan()

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


# what the fold does


def test_the_fold_leaves_one_issue(make_twin):
    """Should be the whole point — one fingerprint, one row, one triage state."""
    make_twin("p-mk1")
    make_twin("p-mk2")

    merge.run()

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_the_fold_keeps_both_environments(make_twin):
    """Should not lose where it fired while merging the rows."""
    make_twin("p-mk1")
    make_twin("p-mk2")

    merge.run()

    result = sorted(
        issue_models.IssueEnvironment.objects.values_list("name", flat=True)
    )
    expected = ["p-mk1", "p-mk2"]

    assert result == expected


def test_the_open_state_wins(make_twin):
    """Should not mark an issue resolved because one cluster's copy was."""
    make_twin("p-mk1", triage_state=issue_models.TriageState.RESOLVED)
    make_twin("p-mk2", triage_state=issue_models.TriageState.NEW)

    merge.run()

    result = issue_models.Issue.objects.get().triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_an_issue_resolved_everywhere_stays_resolved(make_twin):
    """Should not reopen something both clusters had finished with."""
    make_twin("p-mk1", triage_state=issue_models.TriageState.RESOLVED)
    make_twin("p-mk2", triage_state=issue_models.TriageState.RESOLVED)

    merge.run()

    result = issue_models.Issue.objects.get().triage_state
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_the_counts_add_up(make_twin):
    """Should report the issue's real volume, not one cluster's share of it."""
    make_twin("p-mk1", event_count=4)
    make_twin("p-mk2", event_count=7)

    merge.run()

    result = issue_models.Issue.objects.get().event_count
    expected = 11

    assert result == expected


def test_the_window_spans_both_rows(make_twin):
    """Should say the issue started when it first started, on either cluster."""
    first = NOW - datetime.timedelta(days=5)
    make_twin("p-mk1", first_seen=first, last_seen=NOW - datetime.timedelta(days=4))
    make_twin("p-mk2", first_seen=NOW - datetime.timedelta(hours=2), last_seen=NOW)

    merge.run()

    issue = issue_models.Issue.objects.get()
    result = (issue.first_seen, issue.last_seen)
    expected = (first, NOW)

    assert result == expected


def test_an_awake_copy_unsnoozes_the_merged_issue(make_twin):
    """Should not silence an issue because one cluster's copy was snoozed."""
    make_twin("p-mk1", snoozed_until=NOW + datetime.timedelta(hours=4))
    make_twin("p-mk2")

    merge.run()

    result = issue_models.Issue.objects.get().snoozed_until

    assert result is None


def test_episodes_follow_the_keeper(make_twin):
    """Should not orphan the alert history the losing row was carrying."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    issue_models.Episode.objects.create(
        project=loser.project,
        issue=loser,
        am_fingerprint="abc123",
        labels={},
        environment="p-mk2",
        starts_at=NOW,
    )

    merge.run()

    result = issue_models.Episode.objects.get().issue_id
    expected = keeper.pk

    assert result == expected


def test_hourly_counts_are_summed_not_duplicated(make_twin):
    """Should keep one row per hour, holding what both rows saw in it."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    hour = NOW.replace(minute=0, second=0, microsecond=0)
    issue_models.HourlyStat.objects.create(issue=keeper, hour=hour, count=3)
    issue_models.HourlyStat.objects.create(issue=loser, hour=hour, count=5)

    merge.run()

    row = issue_models.HourlyStat.objects.get()
    result = (row.issue_id, row.count)
    expected = (keeper.pk, 8)

    assert result == expected


def test_a_tag_seen_on_both_is_counted_once(make_twin):
    """Should add the breakdowns rather than showing the same key twice."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    issue_models.TagStat.objects.create(
        issue=keeper, key="namespace", value="payments", count=2
    )
    issue_models.TagStat.objects.create(
        issue=loser, key="namespace", value="payments", count=6
    )

    merge.run()

    row = issue_models.TagStat.objects.get()
    result = (row.issue_id, row.count)
    expected = (keeper.pk, 8)

    assert result == expected


def test_a_tag_seen_on_only_one_moves_across(make_twin):
    """Should carry the losing row's own values, not only the shared ones."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    issue_models.TagStat.objects.create(
        issue=loser, key="cluster", value="p-mk2", count=4
    )

    merge.run()

    row = issue_models.TagStat.objects.get()
    result = (row.issue_id, row.key)
    expected = (keeper.pk, "cluster")

    assert result == expected


def test_activity_follows_the_keeper(make_twin):
    """Should keep the triage trail, which is what MTTR is computed from."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    issue_models.IssueActivity.objects.create(
        issue=loser,
        kind=issue_models.ActivityKind.RESOLVED,
        actor="dev",
        at=NOW,
    )

    merge.run()

    result = issue_models.IssueActivity.objects.get().issue_id
    expected = keeper.pk

    assert result == expected


def test_a_queued_notification_follows_the_keeper(make_twin):
    """Should not leave a delivery pointing at a row that is about to go."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    destination = notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.NEW],
    )
    notify_models.Delivery.objects.create(
        issue=loser, destination=destination, event=notify_models.NEW, payload={}
    )

    merge.run()

    result = notify_models.Delivery.objects.get().issue_id
    expected = keeper.pk

    assert result == expected


def test_the_keeper_keeps_its_own_assignment(make_twin, django_user_model):
    """Should not end up with two owners for what is now one issue."""
    keeper = make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    loser = make_twin("p-mk2")
    team = people_models.Team.objects.create(name="platform")
    other = people_models.Team.objects.create(name="search")
    people_models.Assignment.objects.create(issue=keeper, team=team)
    people_models.Assignment.objects.create(issue=loser, team=other)

    merge.run()

    row = people_models.Assignment.objects.get()
    result = (row.issue_id, row.team.name)
    expected = (keeper.pk, "platform")

    assert result == expected


def test_three_rows_fold_into_one(make_twin):
    """Should not assume the split was ever only two ways."""
    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=3))
    make_twin("p-mk2")
    make_twin("p-mk3")

    merge.run()

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_second_run_finds_nothing_left(make_twin):
    """Should be safe to run twice, which is what a retried migration does."""
    make_twin("p-mk1")
    make_twin("p-mk2")
    merge.run()

    result = merge.run().groups

    assert result == []


def test_the_report_names_what_it_folded(make_twin):
    """Should let an operator read the dry run rather than trust it."""
    make_twin("p-mk1")
    make_twin("p-mk2")

    lines = merge.plan().lines()

    assert "p-mk1, p-mk2" in lines[0]


# the corners


def test_two_rows_in_the_same_environment_fold_their_counts(make_twin):
    """Should happen when the split was on something other than the cluster."""
    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2), event_count=2)
    make_twin("p-mk1", event_count=5)

    merge.run()

    row = issue_models.IssueEnvironment.objects.get()
    result = (row.name, row.event_count)
    expected = ("p-mk1", 7)

    assert result == expected


def test_a_group_whose_rows_already_went_is_skipped(make_twin):
    """Should survive a retried migration that folded half of it already."""
    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    make_twin("p-mk2")
    plan = merge.plan()
    merge.run()

    merge._fold(plan.groups[0])

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_the_latest_resolution_time_survives(make_twin):
    """Should keep the most recent resolve, which is what regression compares against."""
    latest = NOW - datetime.timedelta(hours=1)
    make_twin(
        "p-mk1",
        first_seen=NOW - datetime.timedelta(days=2),
        triage_state=issue_models.TriageState.RESOLVED,
        last_resolved_at=NOW - datetime.timedelta(days=1),
    )
    make_twin(
        "p-mk2",
        triage_state=issue_models.TriageState.RESOLVED,
        last_resolved_at=latest,
    )

    merge.run()

    result = issue_models.Issue.objects.get().last_resolved_at
    expected = latest

    assert result == expected


def test_an_issue_snoozed_everywhere_stays_snoozed(make_twin):
    """Should not wake something both copies were deliberately quiet about."""
    until = NOW + datetime.timedelta(hours=6)
    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2), snoozed_until=until)
    make_twin("p-mk2", snoozed_until=until)

    merge.run()

    result = issue_models.Issue.objects.get().snoozed_until
    expected = until

    assert result == expected


def test_a_count_snooze_survives_when_both_carry_one(make_twin):
    """Should hold the count form of quiet as well as the time form."""
    make_twin(
        "p-mk1", first_seen=NOW - datetime.timedelta(days=2), snoozed_past_count=100
    )
    make_twin("p-mk2", snoozed_past_count=200)

    merge.run()

    result = issue_models.Issue.objects.get().snoozed_past_count

    assert result in (100, 200)


def test_an_install_with_no_event_table_still_folds(make_twin, mocker):
    """Should not need a store to merge the issue rows an operator can see."""
    mocker.patch.object(
        connection.introspection, "table_names", return_value=["issues_issue"]
    )
    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    make_twin("p-mk2")

    merge.run()

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_the_command_names_each_fingerprint_it_folded(make_twin):
    """Should print the work, so a dry run is something to read rather than trust."""
    import io

    from django.core import management

    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    make_twin("p-mk2")
    out = io.StringIO()

    management.call_command("merge_issues", "--dry-run", stdout=out)

    assert FINGERPRINT[:12] in out.getvalue()


def test_the_command_folds_for_real_without_the_flag(make_twin):
    """Should be the escape hatch when the migration has already run."""
    import io

    from django.core import management

    make_twin("p-mk1", first_seen=NOW - datetime.timedelta(days=2))
    make_twin("p-mk2")

    management.call_command("merge_issues", stdout=io.StringIO())

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected
