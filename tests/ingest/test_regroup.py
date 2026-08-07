import datetime
import io

import pytest
from django.core import management

from pandora.core import models as core_models
from pandora.events import store as events_store
from pandora.ingest import models as ingest_models
from pandora.ingest import processor, regroup
from pandora.ingest.translators import envelope as envelope_translator
from pandora.issues import models as issue_models
from tests.ingest import helpers

RECEIVED_AT = datetime.datetime(2026, 8, 4, 19, 32, tzinfo=datetime.UTC)
STALE_HASH = "f" * 64

pytestmark = pytest.mark.django_db


@pytest.fixture
def event_store(db):
    return events_store.get_store()


def payload(event_id, function="get_json", value="404 for a board", minutes=0):
    stamp = RECEIVED_AT + datetime.timedelta(minutes=minutes)
    return {
        "event_id": event_id,
        "level": "error",
        "timestamp": stamp.isoformat(),
        "exception": {
            "values": [
                {
                    "type": "HTTPError",
                    "value": value,
                    "module": "requests.exceptions",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "listopad.core.transport",
                                "function": function,
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        },
        "tags": {"source": function},
    }


def deliver(project, body, store, minutes=0):
    envelope = ingest_models.RawEnvelope.objects.create(
        project=project,
        source=core_models.TokenSource.SDK,
        environment="p-mk1",
        payload=body,
        received_at=RECEIVED_AT + datetime.timedelta(minutes=minutes),
    )
    processor.process_envelope(envelope.pk, store=store)
    return envelope


@pytest.fixture
def two_call_sites(project, event_store):
    deliver(project, payload("a" * 32, function="get_json"), event_store)
    deliver(project, payload("b" * 32, function="post_json", minutes=5), event_store, 5)
    return project


def collapse(project, event_store):
    """Put every SDK event on one issue, the way the old fingerprint did."""
    rows = event_store.fetch(project.pk, limit=500)
    keeper = issue_models.Issue.objects.order_by("pk").first()
    event_store.reassign_events(project.pk, [row.id for row in rows], keeper.pk)
    issue_models.Issue.objects.exclude(pk=keeper.pk).delete()
    issue_models.Issue.objects.filter(pk=keeper.pk).update(event_count=len(rows))
    keeper.refresh_from_db()
    return keeper


def issue_titles():
    return sorted(issue_models.Issue.objects.values_list("title", flat=True))


def run(**kwargs):
    return regroup.regroup_events(**kwargs)


# reach


def test_a_project_with_no_envelopes_is_left_alone(project, event_store):
    """Should walk a project with nothing retained without touching anything."""
    report = run(store=event_store)

    result = (report.projects, report.issues_before, report.events)
    expected = (1, 0, 0)

    assert result == expected


def test_an_alertmanager_issue_is_never_touched(am_fixture, token, event_store):
    """Should leave episode-backed issues to the episode rebuild."""
    helpers.deliver(am_fixture("firing_group"), token, event_store, RECEIVED_AT)
    before = helpers.snapshot()

    run(store=event_store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


# no-op


def test_regrouping_freshly_ingested_events_writes_nothing(two_call_sites, event_store):
    """Should settle on what live ingest already produced."""
    before = helpers.snapshot()

    run(store=event_store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_regrouping_freshly_ingested_events_reports_no_movement(
    two_call_sites, event_store
):
    """Should report a clean no-op rather than inventing churn."""
    report = run(store=event_store)

    result = (
        report.issues_before,
        report.issues_after,
        report.events_moved,
        report.issues_created,
        report.issues_deleted,
    )
    expected = (2, 2, 0, 0, 0)

    assert result == expected


def test_regrouping_counts_what_it_re_read(two_call_sites, event_store):
    """Should report the envelope history it recomputed from."""
    report = run(store=event_store)

    result = (report.envelopes, report.events, report.unreadable)
    expected = (2, 2, 0)

    assert result == expected


# splitting a collapsed issue


def test_a_collapsed_issue_splits_by_call_site(two_call_sites, event_store):
    """Should undo the old grouping — one class in two places is two issues."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


def test_a_split_sends_each_event_to_its_own_issue(two_call_sites, event_store):
    """Should relink the stored events, which carry no episode to move them by."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = [
        len(event_store.fetch(issue.project_id, issue_id=issue.pk))
        for issue in issue_models.Issue.objects.order_by("pk")
    ]
    expected = [1, 1]

    assert result == expected


def test_a_split_reports_the_events_it_relinked(two_call_sites, event_store):
    """Should say how many stored rows moved."""
    collapse(two_call_sites, event_store)

    report = run(store=event_store)

    result = (report.events_moved, report.issues_created)
    expected = (1, 1)

    assert result == expected


def test_a_split_rebuilds_the_counters(two_call_sites, event_store):
    """Should recount every issue from the events it now owns."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = sorted(issue.event_count for issue in issue_models.Issue.objects.all())
    expected = [1, 1]

    assert result == expected


def test_a_split_rebuilds_the_aggregates(two_call_sites, event_store):
    """Should split the sparkline along with the issues."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = sorted(stat.count for stat in issue_models.HourlyStat.objects.all())
    expected = [1, 1]

    assert result == expected


def test_a_split_rebuilds_the_tag_distribution(two_call_sites, event_store):
    """Should leave each new issue holding only its own tags."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = sorted(
        stat.value for stat in issue_models.TagStat.objects.filter(key="source")
    )
    expected = ["get_json", "post_json"]

    assert result == expected


def test_a_split_titles_each_issue_from_its_own_events(two_call_sites, event_store):
    """Should name each new issue for where it is raised, not for its donor."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = issue_titles()
    expected = [
        "HTTPError: listopad.core.transport in get_json",
        "HTTPError: listopad.core.transport in post_json",
    ]

    assert result == expected


def test_a_split_dates_each_issue_from_its_own_events(two_call_sites, event_store):
    """Should rebuild first_seen per issue, not copy the collapsed span."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = sorted(issue.first_seen for issue in issue_models.Issue.objects.all())
    expected = [RECEIVED_AT, RECEIVED_AT + datetime.timedelta(minutes=5)]

    assert result == expected


def test_a_split_removes_the_issue_nothing_points_at(two_call_sites, event_store):
    """Should clean up the issue whose events all moved away."""
    keeper = collapse(two_call_sites, event_store)
    issue_models.Issue.objects.update(fingerprint_hash=STALE_HASH)

    report = run(store=event_store)

    result = (report.issues_deleted, report.orphans, report.issues_created)
    expected = (1, [keeper.title], 2)

    assert result == expected


def test_a_split_is_recorded_on_each_issue(two_call_sites, event_store):
    """Should leave a trail explaining why an issue's identity changed."""
    collapse(two_call_sites, event_store)

    run(store=event_store)

    result = issue_models.IssueActivity.objects.filter(kind="regrouped").count()
    expected = 1

    assert result == expected


# renaming in place


def test_a_stale_identity_is_rewritten_in_place(project, event_store):
    """Should keep the issue row when its whole event set stays together."""
    deliver(project, payload("a" * 32), event_store)
    issue = issue_models.Issue.objects.get()
    issue_models.Issue.objects.update(fingerprint_hash=STALE_HASH)

    run(store=event_store)

    result = (issue_models.Issue.objects.count(), issue_models.Issue.objects.get().pk)
    expected = (1, issue.pk)

    assert result == expected


def test_a_rename_stores_the_fingerprint_the_translator_now_produces(
    project, event_store
):
    """Should write the identity live ingest would give the same event today."""
    deliver(project, payload("a" * 32), event_store)
    issue_models.Issue.objects.update(fingerprint_hash=STALE_HASH)

    run(store=event_store)

    result = issue_models.Issue.objects.get().fingerprint
    expected = [
        "requests.exceptions",
        "HTTPError",
        "listopad.core.transport",
        "get_json",
    ]

    assert result == expected


def test_a_rename_carries_the_triage_state(project, event_store):
    """Should not throw away an operator's decision when grouping changes."""
    deliver(project, payload("a" * 32), event_store)
    issue_models.Issue.objects.update(
        fingerprint_hash=STALE_HASH,
        triage_state=issue_models.TriageState.ACKNOWLEDGED,
    )

    report = run(store=event_store)

    result = (issue_models.Issue.objects.get().triage_state, report.triage_migrated)
    expected = ("ack", 1)

    assert result == expected


# merging


@pytest.fixture
def one_call_site(project, event_store):
    deliver(project, payload("a" * 32, value="404 for board one"), event_store)
    deliver(
        project,
        payload("b" * 32, value="404 for board two", minutes=5),
        event_store,
        5,
    )
    return project


def scatter(project, event_store):
    """Give every SDK event its own issue, the way a per-value fingerprint did."""
    rows = event_store.fetch(project.pk, limit=500)
    keeper = issue_models.Issue.objects.order_by("pk").first()
    for index, row in enumerate(rows[1:], start=1):
        clone = issue_models.Issue.objects.create(
            project=project,
            environment=keeper.environment,
            fingerprint_hash=f"{index:064d}",
            title=keeper.title,
            level=keeper.level,
            event_count=1,
        )
        event_store.reassign_events(project.pk, [row.id], clone.pk)
    issue_models.Issue.objects.filter(pk=keeper.pk).update(
        event_count=1, fingerprint_hash=STALE_HASH
    )
    return keeper


def test_two_issues_of_one_group_merge(one_call_site, event_store):
    """Should fold the per-URL issues a value-sensitive fingerprint left behind."""
    scatter(one_call_site, event_store)

    run(store=event_store)

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_merge_sums_the_counters(one_call_site, event_store):
    """Should recount the merged issue from both events."""
    scatter(one_call_site, event_store)

    run(store=event_store)

    issue = issue_models.Issue.objects.get()
    result = (
        issue.event_count,
        len(event_store.fetch(one_call_site.pk, issue_id=issue.pk)),
    )
    expected = (2, 2)

    assert result == expected


def test_a_merge_removes_both_emptied_issues(one_call_site, event_store):
    """Should leave no ghost issues behind once every event has moved."""
    scatter(one_call_site, event_store)

    report = run(store=event_store)

    result = (
        report.issues_before,
        report.issues_after,
        report.issues_created,
        report.issues_deleted,
    )
    expected = (2, 1, 1, 2)

    assert result == expected


def test_a_merge_spans_both_events(one_call_site, event_store):
    """Should widen the merged issue's window over everything it swallowed."""
    scatter(one_call_site, event_store)

    run(store=event_store)

    issue = issue_models.Issue.objects.get()
    result = (issue.first_seen, issue.last_seen)
    expected = (RECEIVED_AT, RECEIVED_AT + datetime.timedelta(minutes=5))

    assert result == expected


# events the backfill cannot see


def test_an_event_whose_envelope_expired_keeps_its_issue(project, event_store):
    """Should leave what it cannot re-read where it is, not delete it."""
    expired = deliver(project, payload("a" * 32), event_store)
    deliver(project, payload("b" * 32, function="post_json", minutes=5), event_store, 5)
    expired.delete()

    run(store=event_store)

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


def test_an_issue_the_backfill_cannot_re_read_keeps_its_identity(project, event_store):
    """Should hand back the parked fingerprint, never leave a regroup- name."""
    expired = deliver(project, payload("a" * 32), event_store)
    deliver(project, payload("b" * 32, function="post_json", minutes=5), event_store, 5)
    stranded = issue_models.Issue.objects.order_by("pk").first()
    before = stranded.fingerprint_hash
    expired.delete()

    run(store=event_store)

    stranded.refresh_from_db()
    result = stranded.fingerprint_hash
    expected = before

    assert result == expected
    assert not result.startswith(regroup.TEMP_PREFIX)


def test_an_unreadable_envelope_is_counted_and_skipped(project, event_store):
    """Should keep going when one retained payload no longer parses."""
    envelope = deliver(project, payload("a" * 32), event_store)
    ingest_models.RawEnvelope.objects.filter(pk=envelope.pk).update(payload=[1, 2, 3])

    report = run(store=event_store)

    result = (report.envelopes, report.unreadable, report.issues_before)
    expected = (1, 1, 0)

    assert result == expected


def test_an_envelope_whose_event_expired_regroups_nothing(project, event_store):
    """Should walk away quietly when the events behind the payloads are gone."""
    deliver(project, payload("a" * 32), event_store)
    event_store.prune(RECEIVED_AT + datetime.timedelta(days=400))

    report = run(store=event_store)

    result = (report.envelopes, report.issues_before, report.issues_after)
    expected = (1, 0, 0)

    assert result == expected


def test_an_issue_with_no_events_left_is_not_read(project, event_store):
    """Should skip an issue the store holds nothing for rather than empty it."""
    deliver(project, payload("a" * 32), event_store)
    issue_models.Issue.objects.create(
        project=project,
        environment="p-mk1",
        fingerprint_hash=STALE_HASH,
        title="nothing points here",
    )

    report = run(store=event_store)

    result = (report.issues_before, issue_models.Issue.objects.count())
    expected = (1, 2)

    assert result == expected


def test_an_issue_wider_than_one_page_is_read_whole(
    one_call_site, event_store, monkeypatch
):
    """Should page through a busy issue instead of reading its first page only."""
    monkeypatch.setattr(regroup, "PAGE", 1)
    scatter(one_call_site, event_store)

    run(store=event_store)

    issue = issue_models.Issue.objects.get()
    result = issue.event_count
    expected = 2

    assert result == expected


def test_a_replay_creates_no_second_event(two_call_sites, event_store):
    """Should re-read the envelopes without pushing them back through ingest."""
    before = ingest_models.ProcessedEvent.objects.count()

    run(store=event_store)

    result = (
        len(event_store.fetch(two_call_sites.pk, limit=500)),
        ingest_models.ProcessedEvent.objects.count(),
    )
    expected = (2, before)

    assert result == expected


# repeatability


def test_regrouping_twice_changes_nothing_the_second_time(two_call_sites, event_store):
    """Should settle on a fixed point — the recovery tool has to be repeatable."""
    collapse(two_call_sites, event_store)
    run(store=event_store)
    before = helpers.snapshot()

    report = run(store=event_store)

    result = helpers.snapshot()
    expected = before

    assert result == expected
    assert report.events_moved == 0


# dry run


def test_a_dry_run_writes_nothing(two_call_sites, event_store):
    """Should let an operator see the split before doing it."""
    collapse(two_call_sites, event_store)
    before = helpers.snapshot()

    run(dry_run=True, store=event_store)

    result = helpers.snapshot()
    expected = before

    assert result == expected


def test_a_dry_run_leaves_the_stored_events_alone(two_call_sites, event_store):
    """Should roll the event store back with everything else."""
    keeper = collapse(two_call_sites, event_store)

    run(dry_run=True, store=event_store)

    result = len(event_store.fetch(two_call_sites.pk, issue_id=keeper.pk))
    expected = 2

    assert result == expected


def test_a_dry_run_reports_what_would_happen(two_call_sites, event_store):
    """Should report exactly the numbers the real run would produce."""
    collapse(two_call_sites, event_store)

    dry = run(dry_run=True, store=event_store)
    applied = run(store=event_store)

    result = (dry.issues_created, dry.events_moved, dry.issues_deleted)
    expected = (applied.issues_created, applied.events_moved, applied.issues_deleted)

    assert result == expected


# project scoping


def test_one_project_can_be_rebuilt_alone(two_call_sites, event_store):
    """Should leave other projects untouched when a slug is given."""
    other = core_models.Project.objects.create(slug="apps", name="Apps")
    deliver(other, payload("c" * 32), event_store)
    collapse(two_call_sites, event_store)

    report = run(project=two_call_sites, store=event_store)

    result = (report.projects, report.envelopes)
    expected = (1, 2)

    assert result == expected


# the deterministic id the backfill joins on


def test_the_re_read_event_id_matches_the_one_ingest_stored(project, event_store):
    """Should rebuild the same stored id, or the backfill joins on nothing."""
    deliver(project, payload("a" * 32), event_store)
    stored = event_store.fetch(project.pk)[0]

    result = envelope_translator.event_id(
        project.pk, "a" * 32, RECEIVED_AT.replace(tzinfo=datetime.UTC)
    )
    expected = stored.id

    assert result == expected


# the command


def test_the_command_reports_the_sdk_rebuild(two_call_sites, event_store):
    """Should tell the operator what the envelope pass did, not only the episodes."""
    collapse(two_call_sites, event_store)
    out = io.StringIO()

    management.call_command("regroup", stdout=out)

    assert "regroup: sdk — rebuilt 1 issues into 2" in out.getvalue()


def test_the_command_says_when_it_only_looked(two_call_sites, event_store):
    """Should never let a dry run read like an applied change."""
    collapse(two_call_sites, event_store)
    out = io.StringIO()

    management.call_command("regroup", "--dry-run", stdout=out)

    result = issue_models.Issue.objects.count()
    expected = 1

    assert "regroup: sdk — would rebuild" in out.getvalue()
    assert result == expected


def test_the_command_names_the_sdk_issues_it_emptied(two_call_sites, event_store):
    """Should name what it removed so nothing disappears quietly."""
    keeper = collapse(two_call_sites, event_store)
    issue_models.Issue.objects.update(fingerprint_hash=STALE_HASH)
    out = io.StringIO()

    management.call_command("regroup", stdout=out)

    assert f"orphaned {keeper.title}" in out.getvalue()
