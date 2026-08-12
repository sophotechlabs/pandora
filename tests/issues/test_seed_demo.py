import io

import freezegun
import pytest
from django.core import management
from django.db import models as db_models

from pandora.core import models as core_models
from pandora.events.store import get_store
from pandora.issues import models
from tests.ingest import helpers

pytestmark = pytest.mark.django_db

FROZEN = "2026-08-04 14:00:00"


def run_seed_demo(force=False):
    out = io.StringIO()
    management.call_command("seed_demo", stdout=out, force=force)
    return out.getvalue()


@pytest.fixture
def seeded():
    with freezegun.freeze_time(FROZEN):
        run_seed_demo()


# shape


def test_seed_demo_creates_the_two_demo_projects(seeded):
    """Should create exactly the demo-prefixed projects, never a real one."""
    result = sorted(core_models.Project.objects.values_list("slug", flat=True))
    expected = ["demo-apps", "demo-infra"]

    assert result == expected


def test_seed_demo_gives_every_project_an_ingest_and_a_read_token(seeded):
    """Should mint one Alertmanager ingest token and one read token per project."""
    result = sorted(
        core_models.IngestToken.objects.values_list("source", "scope").distinct()
    )
    expected = [("am", "ingest"), ("sdk", "read")]

    assert result == expected


def test_seed_demo_covers_every_triage_state(seeded):
    """Should seed at least one issue in each triage state so filters have data."""
    result = sorted(set(models.Issue.objects.values_list("triage_state", flat=True)))
    expected = sorted(models.TriageState.values)

    assert result == expected


def test_seed_demo_covers_both_source_states(seeded):
    """Should seed both firing and self-resolved issues."""
    result = sorted(set(models.Issue.objects.values_list("source_state", flat=True)))
    expected = ["firing", "resolved"]

    assert result == expected


def test_seed_demo_seeds_open_and_closed_episodes(seeded):
    """Should leave some episodes open and close the rest."""
    result = {
        "open": models.Episode.objects.filter(ends_at__isnull=True).count() > 0,
        "closed": models.Episode.objects.filter(ends_at__isnull=False).count() > 0,
    }
    expected = {"open": True, "closed": True}

    assert result == expected


def test_seed_demo_records_a_regression(seeded):
    """Should seed a regression so the activity feed has one to render."""
    result = models.IssueActivity.objects.filter(
        kind=models.ActivityKind.REGRESSION
    ).count()
    expected = 1

    assert result == expected


# aggregate invariants


def test_event_count_equals_the_episode_count(seeded):
    """Should count one occurrence per episode created — never per delivery."""
    result = [
        (issue.event_count, issue.episodes.count())
        for issue in models.Issue.objects.all()
    ]

    assert all(counted == actual for counted, actual in result)


def test_open_episode_count_matches_the_open_episodes(seeded):
    """Should keep the stored open counter equal to the rows it summarises."""
    result = [
        (issue.open_episode_count, issue.episodes.filter(ends_at__isnull=True).count())
        for issue in models.Issue.objects.all()
    ]

    assert all(counted == actual for counted, actual in result)


def test_source_state_is_derived_from_the_open_counter(seeded):
    """Should mark an issue firing if and only if it has an open episode."""
    result = [
        (issue.source_state == models.SourceState.FIRING, issue.open_episode_count > 0)
        for issue in models.Issue.objects.all()
    ]

    assert all(firing == has_open for firing, has_open in result)


def test_the_sparkline_buckets_sum_to_the_event_count(seeded):
    """Should keep hourly buckets consistent with the issue counter."""
    result = [
        (
            issue.event_count,
            issue.hourly_stats.aggregate(total=db_models.Sum("count"))["total"],
        )
        for issue in models.Issue.objects.all()
    ]

    assert all(counted == bucketed for counted, bucketed in result)


def test_the_alertname_tag_sums_to_the_event_count(seeded):
    """Should count every episode once under the label every instance carries."""
    result = [
        (
            issue.event_count,
            issue.tag_stats.filter(key="alertname").aggregate(
                total=db_models.Sum("count")
            )["total"],
        )
        for issue in models.Issue.objects.all()
    ]

    assert all(counted == tagged for counted, tagged in result)


def test_first_and_last_seen_bracket_the_episodes(seeded):
    """Should bound the issue window by its own episodes."""
    result = []
    for issue in models.Issue.objects.all():
        window = issue.episodes.aggregate(
            earliest=db_models.Min("starts_at"),
            latest=db_models.Max("last_delivery_at"),
        )
        result.append(
            (
                issue.first_seen == window["earliest"],
                issue.last_seen == window["latest"],
            )
        )

    assert all(first and last for first, last in result)


# idempotency


def test_seed_demo_is_idempotent(seeded):
    """Should replace demo data rather than accumulate it on a second run."""
    before = models.Episode.objects.count()

    with freezegun.freeze_time(FROZEN):
        run_seed_demo()

    result = models.Episode.objects.count()
    expected = before

    assert result == expected


def test_seed_demo_leaves_non_demo_projects_alone(project):
    """Should never touch a project that is not part of the demo set."""
    with freezegun.freeze_time(FROZEN):
        run_seed_demo(force=True)

    assert core_models.Project.objects.filter(slug="infrastructure").exists() is True


def test_seed_demo_reports_what_it_wrote(seeded):
    """Should print the counts it created so a human can sanity-check them."""
    with freezegun.freeze_time(FROZEN):
        result = run_seed_demo()

    assert result.startswith("seed_demo: 2 projects, 6 issues, ")


# guard against seeding a real database


def test_seed_demo_refuses_a_database_with_real_projects(project):
    """Should not overwrite demo data into somebody's live instance."""
    with pytest.raises(management.CommandError, match="already holds real"):
        run_seed_demo()


def test_seed_demo_refuses_a_database_with_ingested_envelopes(token, am_fixture):
    """Should treat any ingested envelope as proof this is not a scratch database."""
    helpers.store_envelope(am_fixture("firing_group"), token)

    with pytest.raises(management.CommandError, match="already holds real"):
        run_seed_demo()


def test_seed_demo_runs_on_an_empty_database():
    """Should stay a one-command demo on a scratch database."""
    result = run_seed_demo()

    assert result.startswith("seed_demo: 2 projects")


def test_demo_tokens_are_not_guessable(seeded):
    """Should not ship a token an outsider could guess from the project slug."""
    tokens = list(core_models.IngestToken.objects.values_list("token", flat=True))

    assert tokens
    for token in tokens:
        assert token not in ("demo-am-payments", "demo-read-payments")
        assert len(token) > 30


def test_the_seed_stores_one_event_per_episode(seeded):
    """Should give the occurrences tab something to show on a fresh install."""
    store = get_store()
    for issue in models.Issue.objects.all():
        found = store.fetch(issue.project_id, issue_id=issue.pk, limit=1000)

        assert len(found) == issue.episodes.count()


def test_a_seeded_event_carries_the_labels_of_its_episode(seeded):
    """Should let the occurrence viewer show what one delivery actually said."""
    issue = models.Issue.objects.get(title__startswith="KubePodCrashLooping")
    store = get_store()

    found = store.fetch(issue.project_id, issue_id=issue.pk, limit=1)

    result = found[0].tags["namespace"]
    expected = "payments"

    assert result == expected


def test_a_seeded_event_carries_the_alert_summary(seeded):
    """Should read as an occurrence, not as a bare identifier."""
    issue = models.Issue.objects.get(title__startswith="KubePodCrashLooping")
    store = get_store()

    found = store.fetch(issue.project_id, issue_id=issue.pk, limit=1)

    result = found[0].message
    expected = "Pod payments/ledger is in CrashLoopBackOff"

    assert result == expected


def test_seeded_events_are_stored_in_time_order(seeded):
    """Should let the newest-first occurrence list actually read newest first."""
    issue = models.Issue.objects.get(title__startswith="TargetDown")

    found = get_store().fetch(issue.project_id, issue_id=issue.pk, limit=5)

    result = [event.timestamp for event in found]
    expected = sorted(result, reverse=True)

    assert result == expected
