import copy
import datetime
import io

import pytest
from django.core import management
from django.core.management import base as management_base

from pandora.core import models as core_models
from pandora.events import store as events_store
from pandora.issues import grouping, models, regroup
from tests.ingest import helpers

RECEIVED_AT = datetime.datetime(2026, 8, 4, 9, 15, tzinfo=datetime.UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture
def event_store(db):
    return events_store.get_store()


@pytest.fixture
def ingested(am_fixture, token, event_store):
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    return models.Issue.objects.get()


def stored_pairs(event_store, project_id):
    rows = event_store.fetch(project_id)
    return sorted((row.episode_id, row.issue_id) for row in rows)


def warning_copy(payload):
    other = copy.deepcopy(payload)
    for index, alert in enumerate(other["alerts"]):
        alert["labels"]["severity"] = "warning"
        alert["fingerprint"] = f"aaaa{index:012d}"
    return other


def group_on_alertname_only():
    models.GroupingRule.objects.create(
        priority=10,
        mode=models.GroupingMode.ALLOWLIST,
        labels=["alertname"],
    )


def group_on_every_label():
    models.GroupingRule.objects.update(labels=[])


def run(**kwargs):
    return regroup.regroup(**kwargs)


# no-op


def test_regrouping_unchanged_rules_writes_nothing(ingested):
    """Should leave the database exactly as it was when nothing regroups."""
    before = helpers.snapshot()

    run()

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_regrouping_unchanged_rules_reports_no_movement(ingested):
    """Should report a clean no-op rather than inventing churn."""
    report = run()

    result = (
        report.issues_before,
        report.issues_after,
        report.episodes_moved,
        report.issues_created,
        report.issues_deleted,
    )
    expected = (1, 1, 0, 0, 0)

    assert result == expected


def test_regrouping_counts_what_it_walked(ingested):
    """Should report the episode history it recomputed from."""
    report = run()

    result = (report.projects, report.episodes)
    expected = (1, 2)

    assert result == expected


def test_a_project_with_no_episodes_is_left_alone(ingested):
    """Should skip a project whose history is empty instead of failing."""
    core_models.Project.objects.create(slug="empty", name="Empty")

    report = run()

    result = (report.projects, report.issues_before)
    expected = (2, 1)

    assert result == expected


# renaming in place


def test_a_wider_rule_renames_the_issue_in_place(ingested):
    """Should keep the issue row when its whole episode set stays together."""
    group_on_alertname_only()

    run()

    result = models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_rename_keeps_the_issue_primary_key(ingested):
    """Should preserve activity, silences and triage by never recreating the row."""
    group_on_alertname_only()

    run()

    result = models.Issue.objects.get().pk
    expected = ingested.pk

    assert result == expected


def test_a_rename_rewrites_the_grouping_identity(ingested):
    """Should store the fingerprint the new rule produces."""
    group_on_alertname_only()

    run()
    issue = models.Issue.objects.get()

    result = (issue.fingerprint, issue.grouping_labels, issue.culprit)
    expected = (
        ["alertname:KubePodCrashLooping"],
        {"alertname": "KubePodCrashLooping"},
        "alertname=KubePodCrashLooping",
    )

    assert result == expected


def test_a_rename_carries_the_triage_state(ingested):
    """Should not throw away an operator's decision when grouping changes."""
    models.Issue.objects.update(triage_state=models.TriageState.ACKNOWLEDGED)
    group_on_alertname_only()

    report = run()

    result = (models.Issue.objects.get().triage_state, report.triage_migrated)
    expected = ("ack", 1)

    assert result == expected


def test_a_rename_keeps_the_human_title(ingested):
    """Should keep the readable title — episodes carry labels, not annotations."""
    group_on_alertname_only()

    run()

    result = models.Issue.objects.get().title
    expected = ingested.title

    assert result == expected


def test_a_rename_is_recorded_on_the_issue(ingested):
    """Should leave a trail explaining why an issue's identity changed."""
    group_on_alertname_only()

    run()

    result = [
        activity.kind for activity in models.IssueActivity.objects.order_by("kind")
    ]
    expected = ["created", "regrouped"]

    assert result == expected


# splitting


def test_a_narrower_rule_splits_the_issue(ingested):
    """Should give each pod its own issue when the rule stops hiding the label."""
    group_on_every_label()

    run()

    result = models.Issue.objects.count()
    expected = 2

    assert result == expected


def test_a_split_moves_every_episode(ingested):
    """Should reassign each episode to the issue its labels now belong to."""
    group_on_every_label()

    report = run()

    result = (report.episodes_moved, report.issues_created)
    expected = (2, 2)

    assert result == expected


def test_a_split_deletes_the_issue_nothing_points_at(ingested):
    """Should clean up the issue whose episodes all moved away."""
    group_on_every_label()

    report = run()

    result = (report.issues_deleted, report.orphans)
    expected = (1, [ingested.title])

    assert result == expected


def test_a_split_rebuilds_the_counters(ingested):
    """Should recount every issue from the episodes it now owns."""
    group_on_every_label()

    run()

    result = sorted(
        (issue.event_count, issue.open_episode_count, issue.source_state)
        for issue in models.Issue.objects.all()
    )
    expected = [(1, 1, "firing"), (1, 1, "firing")]

    assert result == expected


def test_a_split_rebuilds_the_aggregates(ingested):
    """Should split the sparkline along with the issues."""
    group_on_every_label()

    run()

    result = sorted(stat.count for stat in models.HourlyStat.objects.all())
    expected = [1, 1]

    assert result == expected


def test_a_split_rebuilds_the_tag_distribution(ingested):
    """Should leave each new issue holding only its own labels."""
    group_on_every_label()

    run()

    result = sorted(stat.value for stat in models.TagStat.objects.filter(key="pod"))
    expected = ["ledger-7d9f4c8b6d-hk2mp", "ledger-7d9f4c8b6d-x4rtq"]

    assert result == expected


def test_a_split_does_not_carry_triage_state(ingested):
    """Should not spread one triage decision across several new issues."""
    models.Issue.objects.update(triage_state=models.TriageState.ACKNOWLEDGED)
    group_on_every_label()

    report = run()

    result = (
        {issue.triage_state for issue in models.Issue.objects.all()},
        report.triage_migrated,
    )
    expected = ({"new"}, 0)

    assert result == expected


def test_a_split_dates_each_issue_from_its_own_episode(ingested):
    """Should rebuild first_seen and last_seen per issue, not copy the old span."""
    group_on_every_label()

    run()

    result = sorted(issue.first_seen for issue in models.Issue.objects.all())
    expected = sorted(episode.starts_at for episode in models.Episode.objects.all())

    assert result == expected


def test_a_split_of_resolved_episodes_leaves_resolved_issues(
    am_fixture, token, ingested
):
    """Should rebuild source_state from the episodes, not from the old row."""
    helpers.deliver(am_fixture("resolved_group"), token, received_at=RECEIVED_AT)
    group_on_every_label()

    run()

    result = {issue.source_state for issue in models.Issue.objects.all()}
    expected = {"resolved"}

    assert result == expected


def test_two_issues_can_swap_grouping_identities(project, ingested):
    """Should survive a rebuild where one issue claims another's fingerprint."""
    first, second = _swapped_pair(project)

    run()

    result = sorted(
        (issue.pk, issue.episodes.get().am_fingerprint)
        for issue in models.Issue.objects.filter(pk__in=[first.pk, second.pk])
    )
    expected = sorted([(first.pk, "eeee2"), (second.pk, "eeee1")])

    assert result == expected


def _swapped_pair(project):
    first_labels = {"alertname": "Swap", "side": "left"}
    second_labels = {"alertname": "Swap", "side": "right"}
    first_hash = grouping.fingerprint_hash(
        grouping.compute_fingerprint(grouping.default_rule(), first_labels)
    )
    second_hash = grouping.fingerprint_hash(
        grouping.compute_fingerprint(grouping.default_rule(), second_labels)
    )
    first = models.Issue.objects.create(
        project=project, fingerprint_hash=second_hash, title="left"
    )
    second = models.Issue.objects.create(
        project=project, fingerprint_hash=first_hash, title="right"
    )
    models.Episode.objects.create(
        project=project,
        issue=first,
        am_fingerprint="eeee1",
        labels=first_labels,
        starts_at=RECEIVED_AT,
        last_delivery_at=RECEIVED_AT,
    )
    models.Episode.objects.create(
        project=project,
        issue=second,
        am_fingerprint="eeee2",
        labels=second_labels,
        starts_at=RECEIVED_AT,
        last_delivery_at=RECEIVED_AT,
    )
    return first, second


# stored events


def test_a_split_sends_every_event_to_the_issue_its_episode_landed_on(
    ingested, event_store
):
    """Should relink stored events so an issue's history follows its episodes."""
    group_on_every_label()

    regroup.regroup()

    result = stored_pairs(event_store, ingested.project_id)
    expected = sorted(
        (str(episode.pk), episode.issue_id) for episode in models.Episode.objects.all()
    )

    assert result == expected


def test_each_rebuilt_issue_can_read_its_own_events(ingested, event_store):
    """Should answer the API's own query — fetch by issue id — after a rebuild."""
    group_on_every_label()

    regroup.regroup()

    result = [
        len(event_store.fetch(issue.project_id, issue_id=issue.pk))
        for issue in models.Issue.objects.order_by("pk")
    ]
    expected = [1, 1]

    assert result == expected


def test_a_rebuild_reports_the_events_it_relinked(ingested, event_store):
    """Should say how many stored rows moved, not only how many episodes did."""
    group_on_every_label()

    report = regroup.regroup()

    result = report.events_moved
    expected = 2

    assert result == expected


def test_a_no_op_rebuild_relinks_nothing(ingested, event_store):
    """Should not touch the event store when no episode changes issue."""
    report = regroup.regroup(store=event_store)

    result = report.events_moved
    expected = 0

    assert result == expected


def test_a_dry_run_leaves_the_stored_events_alone(ingested, event_store):
    """Should roll the event store back with everything else."""
    group_on_every_label()
    before = stored_pairs(event_store, ingested.project_id)

    regroup.regroup(dry_run=True)

    result = stored_pairs(event_store, ingested.project_id)
    expected = before

    assert result == expected


# merging


def test_a_wider_rule_merges_two_issues(am_fixture, token, ingested):
    """Should fold two severities into one issue when severity stops grouping."""
    helpers.deliver(
        warning_copy(am_fixture("firing_group")), token, received_at=RECEIVED_AT
    )
    group_on_alertname_only()

    run()

    result = models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_merge_sums_the_counters(am_fixture, token, ingested):
    """Should recount the merged issue from all four episodes."""
    helpers.deliver(
        warning_copy(am_fixture("firing_group")), token, received_at=RECEIVED_AT
    )
    group_on_alertname_only()

    run()
    issue = models.Issue.objects.get()

    result = (issue.event_count, issue.open_episode_count, issue.episodes.count())
    expected = (4, 4, 4)

    assert result == expected


def test_a_merge_removes_both_emptied_issues(am_fixture, token, ingested):
    """Should leave no ghost issues behind once every episode has moved."""
    helpers.deliver(
        warning_copy(am_fixture("firing_group")), token, received_at=RECEIVED_AT
    )
    group_on_alertname_only()

    report = run()

    result = (
        report.issues_before,
        report.issues_after,
        report.issues_created,
        report.issues_deleted,
    )
    expected = (2, 1, 1, 2)

    assert result == expected


def test_a_merge_starts_the_joined_issue_untriaged(am_fixture, token, ingested):
    """Should not carry one grouping's triage decision onto a different group."""
    models.Issue.objects.update(triage_state=models.TriageState.IGNORED)
    helpers.deliver(
        warning_copy(am_fixture("firing_group")), token, received_at=RECEIVED_AT
    )
    group_on_alertname_only()

    report = run()

    result = (models.Issue.objects.get().triage_state, report.triage_migrated)
    expected = ("new", 0)

    assert result == expected


# repeatability


def test_regrouping_twice_changes_nothing_the_second_time(ingested):
    """Should settle on a fixed point — the recovery tool has to be repeatable."""
    group_on_every_label()
    run()
    before = helpers.snapshot()

    report = run()

    result = helpers.snapshot()
    expected = before

    assert result == expected
    assert report.episodes_moved == 0


# dry run


def test_a_dry_run_writes_nothing(ingested):
    """Should let an operator see the damage before doing it."""
    group_on_every_label()
    before = helpers.snapshot()

    run(dry_run=True)

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_a_dry_run_reports_what_would_happen(ingested):
    """Should report exactly the numbers the real run would produce."""
    group_on_every_label()

    dry = run(dry_run=True)
    applied = run()

    result = (dry.issues_created, dry.episodes_moved, dry.issues_deleted)
    expected = (applied.issues_created, applied.episodes_moved, applied.issues_deleted)

    assert result == expected


# project scoping


def test_one_project_can_be_rebuilt_alone(am_fixture, token, ingested):
    """Should leave other projects untouched when a slug is given."""
    other_project = core_models.Project.objects.create(slug="apps", name="Apps")
    other_token = core_models.IngestToken.objects.create(
        project=other_project,
        name="apps",
        token="apps-token",
        environment="p-mk2",
    )
    helpers.deliver(am_fixture("truncated"), other_token, received_at=RECEIVED_AT)
    group_on_every_label()

    report = run(project=token.project)

    result = (report.projects, report.issues_before)
    expected = (1, 1)

    assert result == expected


def test_scoping_leaves_the_other_project_grouped_as_it_was(
    am_fixture, token, ingested
):
    """Should not silently regroup a project the operator did not name."""
    other_project = core_models.Project.objects.create(slug="apps", name="Apps")
    other_token = core_models.IngestToken.objects.create(
        project=other_project,
        name="apps",
        token="apps-token",
        environment="p-mk2",
    )
    helpers.deliver(am_fixture("truncated"), other_token, received_at=RECEIVED_AT)
    untouched = models.Issue.objects.get(project=other_project).fingerprint_hash
    group_on_every_label()

    run(project=token.project)

    result = models.Issue.objects.get(project=other_project).fingerprint_hash
    expected = untouched

    assert result == expected


# the command


def test_the_command_reports_the_rebuild(ingested):
    """Should tell the operator what it did, in one readable line."""
    group_on_every_label()
    out = io.StringIO()

    management.call_command("regroup", stdout=out)

    assert "regroup: rebuilt 1 issues into 2 from 2 episodes" in out.getvalue()


def test_the_command_says_when_it_only_looked(ingested):
    """Should never let a dry run read like an applied change."""
    group_on_every_label()
    out = io.StringIO()

    management.call_command("regroup", "--dry-run", stdout=out)

    result = models.Issue.objects.count()
    expected = 1

    assert "would rebuild" in out.getvalue()
    assert result == expected


def test_the_command_lists_the_issues_it_emptied(ingested):
    """Should name what it removed so nothing disappears quietly."""
    group_on_every_label()
    out = io.StringIO()

    management.call_command("regroup", stdout=out)

    assert f"orphaned {ingested.title}" in out.getvalue()


def test_the_command_can_target_one_project(ingested, token):
    """Should accept the project slug an operator would type."""
    out = io.StringIO()

    management.call_command("regroup", "--project", token.project.slug, stdout=out)

    assert "across 1 projects" in out.getvalue()


def test_the_command_refuses_an_unknown_project(ingested):
    """Should fail loudly on a typo rather than rebuilding everything."""
    with pytest.raises(management_base.CommandError) as error:
        management.call_command("regroup", "--project", "nope")

    result = str(error.value)
    expected = "no project with slug 'nope'"

    assert result == expected


# a grouping change must not strand an episode


@pytest.fixture
def regrouped_live(am_fixture, token, event_store):
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    group_on_alertname_only()
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    return models.Issue.objects.order_by("pk")


def test_a_rule_change_moves_the_episode_to_its_new_issue(regrouped_live):
    """Should follow the grouping, or the episode strands where nothing can reach it."""
    result = [issue.episodes.count() for issue in regrouped_live]
    expected = [0, 2]

    assert result == expected


def test_the_abandoned_issue_stops_claiming_to_be_firing(regrouped_live):
    """Should not leave a phantom firing issue inflating the dashboard forever."""
    old = regrouped_live[0]

    result = (old.open_episode_count, old.source_state)
    expected = (0, models.SourceState.RESOLVED)

    assert result == expected


def test_the_new_issue_owns_the_open_episodes(regrouped_live):
    """Should carry the open count across with the episodes."""
    new = regrouped_live[1]

    result = (new.open_episode_count, new.source_state)
    expected = (2, models.SourceState.FIRING)

    assert result == expected


def test_regroup_survives_an_issue_that_already_holds_the_target_digest(
    regrouped_live, event_store
):
    """Should repair, not raise — this is the case the repair exists for."""
    report = run(store=event_store)

    assert report.episodes == 2


def test_regroup_still_repairs_a_stranded_episode(am_fixture, token, event_store):
    """Should move an episode left behind by an older build."""
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    stranded = models.Issue.objects.get()
    orphan = models.Issue.objects.create(
        project=stranded.project,
        fingerprint_hash="f" * 64,
        title="orphan",
        level=models.Level.ERROR,
    )
    models.Episode.objects.update(issue=orphan)

    run(store=event_store)

    result = models.Episode.objects.filter(issue=stranded).count()
    expected = 2

    assert result == expected


def test_a_closed_episode_moves_without_touching_open_counts(
    am_fixture, token, event_store
):
    """Should move a resolved episode too, and leave both open counts alone."""
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    helpers.deliver(am_fixture("resolved_group"), token, event_store, RECEIVED_AT)
    group_on_alertname_only()

    helpers.deliver(am_fixture("resolved_group"), token, event_store, RECEIVED_AT)

    issues = list(models.Issue.objects.order_by("pk"))
    result = [(issue.episodes.count(), issue.open_episode_count) for issue in issues]
    expected = [(0, 0), (2, 0)]

    assert result == expected
