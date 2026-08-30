import datetime

import pytest
from django.utils import timezone

from pandora.releases import models as release_models
from pandora.releases import service

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def seen(project):
    def build(version, environment="p-mk1", at=None, dist=""):
        return service.record(project, version, dist, environment, at or NOW)

    return build


# recording what is running


def test_an_event_puts_its_release_on_the_map(seen):
    """Should need no marker from CI — the process reports what it is running."""
    seen("1.2.3")

    result = release_models.Release.objects.get().version
    expected = "1.2.3"

    assert result == expected


def test_a_release_is_recorded_once_per_project(seen):
    """Should not mint a row per event."""
    seen("1.2.3")
    seen("1.2.3")

    result = release_models.Release.objects.count()
    expected = 1

    assert result == expected


def test_the_count_moves_with_every_event(seen):
    """Should say how much traffic a release actually carried."""
    seen("1.2.3")
    seen("1.2.3")

    result = release_models.Release.objects.get().event_count
    expected = 2

    assert result == expected


def test_an_empty_release_records_nothing(seen):
    """Should not create a release for an SDK that never set one."""
    result = seen("")

    assert result is None and release_models.Release.objects.count() == 0


def test_a_dist_is_part_of_the_identity(seen):
    """Should keep two builds of one version apart, which is what dist is for."""
    seen("1.2.3", dist="arm64")
    seen("1.2.3", dist="amd64")

    result = release_models.Release.objects.count()
    expected = 2

    assert result == expected


def test_the_sort_key_is_stored(seen):
    """Should order in the database, which is where the comparison happens."""
    seen("1.2.3")

    result = release_models.Release.objects.get().sort_key

    assert result


def test_an_unparseable_version_is_marked(seen):
    """Should let the UI say the ordering is alphabetical rather than real."""
    seen("9f2c1ab")

    result = release_models.Release.objects.get().parsed

    assert result is False


# the rollout


def test_each_environment_gets_its_own_window(seen):
    """Should show a rollout that reached staging and stalled before production."""
    seen("1.2.3", environment="staging", at=NOW - datetime.timedelta(hours=3))
    seen("1.2.3", environment="p-mk1", at=NOW)

    release = release_models.Release.objects.get()
    result = sorted(row.name for row in service.rollout(release))
    expected = ["p-mk1", "staging"]

    assert result == expected


def test_the_window_spans_every_environment(seen):
    """Should say when the release started rolling and when it was last seen."""
    first = NOW - datetime.timedelta(hours=5)
    seen("1.2.3", environment="staging", at=first)
    seen("1.2.3", environment="p-mk1", at=NOW)

    opened, closed = service.window(release_models.Release.objects.get())

    assert opened == first and closed == NOW


def test_an_earlier_event_moves_first_seen_back(seen):
    """Should not claim the release started when pandora first noticed it."""
    earlier = NOW - datetime.timedelta(days=1)
    seen("1.2.3", at=NOW)
    seen("1.2.3", at=earlier)

    result = release_models.Release.objects.get().first_seen
    expected = earlier

    assert result == expected


def test_the_previous_release_is_the_one_below_it(seen):
    """Should be what a rollout window is measured against."""
    seen("1.2.2")
    seen("1.2.3")
    current = release_models.Release.objects.get(version="1.2.3")

    result = service.previous(current).version
    expected = "1.2.2"

    assert result == expected


def test_the_first_release_has_no_previous(seen):
    """Should answer rather than raise on the first deploy an install sees."""
    seen("1.2.3")

    result = service.previous(release_models.Release.objects.get())

    assert result is None


def test_the_latest_release_is_the_highest(seen):
    """Should not be the most recently seen, which flaps during a rollout."""
    seen("1.2.10")
    seen("1.2.9")

    result = service.latest(seen("1.2.9").project).version
    expected = "1.2.10"

    assert result == expected


# deploys


def test_a_deploy_that_never_finished_is_visible(seen, project):
    """Should be a fact on the page rather than something nobody notices."""
    release = seen("1.2.3")
    release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=NOW - datetime.timedelta(hours=3),
    )

    result = len(service.stalled(project, NOW))
    expected = 1

    assert result == expected


def test_a_recent_deploy_is_not_stalled(seen, project):
    """Should give a rollout an hour before calling it stuck."""
    release = seen("1.2.3")
    release_models.Deploy.objects.create(
        release=release, environment="p-mk1", started_at=NOW
    )

    result = service.stalled(project, NOW)

    assert result == []


def test_timing_out_marks_the_state(seen):
    """Should record what happened rather than leaving it started forever."""
    release = seen("1.2.3")
    release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=NOW - datetime.timedelta(hours=3),
    )

    service.time_out(NOW)

    result = release_models.Deploy.objects.get().state
    expected = release_models.DeployState.TIMED_OUT

    assert result == expected


def test_a_deploy_reads_as_where_it_went(seen):
    """Should be legible in the admin without following the id."""
    release = seen("1.2.3")
    deploy = release_models.Deploy.objects.create(release=release, environment="p-mk1")

    result = str(deploy)

    assert "p-mk1" in result and "started" in result


# suspect deploy


def test_the_last_deploy_before_the_issue_is_the_suspect(seen, issue):
    """Should answer the question people actually ask, with no repository access."""
    release = seen("1.2.3")
    older = release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=issue.first_seen - datetime.timedelta(hours=8),
    )
    wanted = release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=issue.first_seen - datetime.timedelta(minutes=5),
    )
    release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=issue.first_seen + datetime.timedelta(hours=1),
    )

    result = service.suspect_deploy(issue).pk
    expected = wanted.pk

    assert result == expected and older.pk != wanted.pk


def test_an_issue_older_than_every_deploy_has_no_suspect(seen, issue):
    """Should say nothing rather than blame the first deploy ever made."""
    release = seen("1.2.3")
    release_models.Deploy.objects.create(
        release=release,
        environment="p-mk1",
        started_at=issue.first_seen + datetime.timedelta(hours=1),
    )

    result = service.suspect_deploy(issue)

    assert result is None


# the corners


def test_a_release_with_a_dist_reads_as_both(seen):
    """Should be pickable from a list where two builds share a version."""
    result = str(seen("1.2.3", dist="arm64"))
    expected = "1.2.3 (arm64)"

    assert result == expected


def test_a_release_environment_reads_as_where_it_ran(seen):
    """Should be legible in the admin without following the id."""
    release = seen("1.2.3", environment="p-mk1")

    result = str(release.environments.get())

    assert "p-mk1" in result


def test_a_resolution_at_a_release_reads_as_that_release(project, issue, seen):
    """Should distinguish the promise from the statement of fact."""
    release = seen("1.2.3")
    service.resolve_in(issue, release=release, at=NOW)

    result = str(release_models.Resolution.objects.get())

    assert "resolved in" in result and "next" not in result


def test_the_previous_release_can_be_scoped_to_an_environment(seen, project):
    """Should compare against what production actually ran, not what staging saw."""
    seen("1.2.2", environment="p-mk1")
    seen("1.2.3", environment="staging")
    seen("1.2.4", environment="p-mk1")
    current = release_models.Release.objects.get(version="1.2.4")

    result = service.previous(current, environment="p-mk1").version
    expected = "1.2.2"

    assert result == expected


def test_the_latest_release_can_be_scoped_to_an_environment(seen, project):
    """Should answer 'what is production on' rather than 'what exists'."""
    seen("1.2.4", environment="staging")
    seen("1.2.2", environment="p-mk1")

    result = service.latest(project, environment="p-mk1").version
    expected = "1.2.2"

    assert result == expected


def test_an_issue_resolved_with_no_release_regresses_on_anything(project, issue):
    """Should not let an empty boundary silently suppress every regression."""
    service.resolve_in(issue, release=None, at=NOW)

    result = service.regressed(issue, "1.2.3")

    assert result is True


def test_a_resolution_carries_its_boundary(project, issue, seen):
    """Should store the release's own sort key, which is what the comparison reads."""
    release = seen("1.2.3")
    service.resolve_in(issue, release=release, at=NOW)

    result = release_models.Resolution.objects.get().sort_key
    expected = release.sort_key

    assert result == expected
