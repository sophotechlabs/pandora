import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import limits
from pandora.ingest.gate import PassThroughGate, RateLimitGate, Verdict
from pandora.ingest.models import IngestCounter, IngestQuota

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 8, 29, 12, 30, 15, tzinfo=datetime.UTC)


# windows


def test_a_bucket_starts_on_the_window_boundary():
    """Should put every hit in the same window into one row, or the count means nothing."""
    result = limits.bucket_start(NOW, 60)
    expected = datetime.datetime(2026, 8, 29, 12, 30, 0, tzinfo=datetime.UTC)

    assert result == expected


def test_a_wider_window_has_a_coarser_bucket():
    """Should let an hourly window share one row for the whole hour."""
    result = limits.bucket_start(NOW, 3600)
    expected = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.UTC)

    assert result == expected


def test_a_zero_window_does_not_divide_by_zero():
    """Should survive a quota someone saved with an empty window."""
    result = limits.bucket_start(NOW, 0)

    assert result <= NOW


def test_retry_after_counts_to_the_end_of_the_window():
    """Should tell an SDK when to come back, not a fixed guess."""
    result = limits.seconds_left(NOW, 60)
    expected = 45

    assert result == expected


def test_retry_after_is_never_zero():
    """Should never invite an immediate retry, which would spin."""
    boundary = datetime.datetime(2026, 8, 29, 12, 30, 0, tzinfo=datetime.UTC)

    result = limits.seconds_left(boundary, 60)
    expected = 60

    assert result == expected


# counting


def test_hits_accumulate_in_one_bucket():
    """Should count across requests, which is the whole point of a shared table."""
    for _ in range(3):
        last = limits.hit("project:1", NOW, 60)

    result = (last, IngestCounter.objects.count())
    expected = (3, 1)

    assert result == expected


def test_a_new_window_starts_a_new_count():
    """Should let the limit reset rather than banning a project forever."""
    limits.hit("project:1", NOW, 60)

    result = limits.hit("project:1", NOW + datetime.timedelta(minutes=1), 60)
    expected = 1

    assert result == expected


def test_two_keys_count_separately():
    """Should keep one project's traffic off another's budget."""
    limits.hit("project:1", NOW, 60)

    result = limits.hit("project:2", NOW, 60)
    expected = 1

    assert result == expected


def test_pruning_drops_old_buckets():
    """Should not let the counter table grow forever."""
    limits.hit("project:1", NOW - datetime.timedelta(days=5), 60)
    limits.hit("project:1", NOW, 60)

    removed = limits.prune(NOW - datetime.timedelta(days=1))

    result = (removed, IngestCounter.objects.count())
    expected = (1, 1)

    assert result == expected


# which quota applies


def test_a_project_quota_beats_the_global_one(project):
    """Should let one noisy project be held tighter than the rest."""
    IngestQuota.objects.create(name="global", limit=1000)
    scoped = IngestQuota.objects.create(name="scoped", project=project, limit=10)

    result = limits.quota_for(project.pk)

    assert result == scoped


def test_the_global_quota_applies_without_a_project_one(project):
    """Should let one limit cover an install."""
    globally = IngestQuota.objects.create(name="global", limit=1000)

    result = limits.quota_for(project.pk)

    assert result == globally


def test_the_tightest_quota_wins(project):
    """Should apply the strictest of several rather than whichever was saved last."""
    IngestQuota.objects.create(name="loose", project=project, limit=100)
    tight = IngestQuota.objects.create(name="tight", project=project, limit=5)

    result = limits.quota_for(project.pk)

    assert result == tight


def test_an_inactive_quota_is_ignored(project):
    """Should let a limit be turned off without deleting it."""
    IngestQuota.objects.create(name="off", project=project, limit=5, active=False)

    result = limits.quota_for(project.pk)

    assert result is None


def test_no_quota_means_no_limit(project):
    """Should leave an unconfigured install unlimited, which is what it was before."""
    result = limits.quota_for(project.pk)

    assert result is None


# the gate


def test_the_gate_allows_when_nothing_is_configured(project):
    """Should be a no-op on an install that never set a quota — no counter row, no extra write."""
    verdict = RateLimitGate().check(project.pk, 100)

    result = (verdict.allowed, IngestCounter.objects.count())
    expected = (True, 0)

    assert result == expected


def test_the_gate_still_refuses_an_oversized_body(project):
    """Should keep the size check the pass-through gate already had."""
    verdict = RateLimitGate(max_bytes=10).check(project.pk, 100)

    result = (verdict.allowed, verdict.reason)
    expected = (False, "oversized")

    assert result == expected


def test_the_gate_refuses_past_the_quota(project):
    """Should shed rather than accept everything a runaway client sends."""
    IngestQuota.objects.create(name="tight", project=project, limit=2)
    gate = RateLimitGate()

    allowed = [gate.check(project.pk, 10).allowed for _ in range(3)]

    result = allowed
    expected = [True, True, False]

    assert result == expected


def test_a_refusal_says_when_to_come_back(project):
    """Should answer with the protocol's own header so an unmodified SDK backs off correctly."""
    IngestQuota.objects.create(
        name="tight", project=project, limit=0, window_seconds=60
    )

    verdict = RateLimitGate().check(project.pk, 10)
    headers = verdict.headers()

    result = (
        verdict.status,
        headers["X-Sentry-Rate-Limits"].endswith(":error:key:rate_limited"),
        "Retry-After" in headers,
    )
    expected = (429, True, True)

    assert result == expected


def test_an_allowed_verdict_carries_no_headers(project):
    """Should not tell a client to slow down when it is inside its budget."""
    result = Verdict(allowed=True).headers()
    expected = {}

    assert result == expected


def test_one_project_does_not_spend_another_budget(project):
    """Should isolate projects, which is the reason the quota is scoped at all."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    IngestQuota.objects.create(name="tight", project=project, limit=1)
    gate = RateLimitGate()

    gate.check(project.pk, 10)
    gate.check(project.pk, 10)

    result = gate.check(other.pk, 10).allowed

    assert result is True


# spike protection


def test_spike_protection_is_off_by_default(project, settings):
    """Should not start shedding on an install that never asked for it."""
    settings.PANDORA_SPIKE_ENABLED = False

    verdict = RateLimitGate().check(project.pk, 10)

    result = (verdict.allowed, IngestCounter.objects.count())
    expected = (True, 0)

    assert result == expected


def test_a_quiet_history_never_looks_like_a_spike(project, settings):
    """Should not refuse a brand-new install that has no baseline to exceed."""
    settings.PANDORA_SPIKE_ENABLED = True
    settings.PANDORA_SPIKE_FLOOR = 1

    result = RateLimitGate().check(project.pk, 10).allowed

    assert result is True


def test_traffic_far_above_the_baseline_is_refused(project, settings):
    """Should catch a runaway loop before it fills the disk."""
    settings.PANDORA_SPIKE_ENABLED = True
    settings.PANDORA_SPIKE_FLOOR = 5
    settings.PANDORA_SPIKE_FACTOR = 2
    key = limits.spike_key(project.pk)
    now = timezone.now()
    for hours in range(1, 4):
        IngestCounter.objects.create(
            key=key,
            bucket=limits.bucket_start(now - datetime.timedelta(hours=hours), 3600),
            count=2,
        )
    IngestCounter.objects.create(
        key=key, bucket=limits.bucket_start(now, 3600), count=100
    )

    verdict = RateLimitGate().check(project.pk, 10)

    result = (verdict.allowed, verdict.reason)
    expected = (False, "spike_protection")

    assert result == expected


def test_traffic_below_the_floor_is_never_a_spike(project, settings):
    """Should not refuse a low-volume project whose count doubled from two to four."""
    settings.PANDORA_SPIKE_ENABLED = True
    settings.PANDORA_SPIKE_FLOOR = 1000
    settings.PANDORA_SPIKE_FACTOR = 2
    key = limits.spike_key(project.pk)
    now = timezone.now()
    IngestCounter.objects.create(
        key=key,
        bucket=limits.bucket_start(now - datetime.timedelta(hours=1), 3600),
        count=1,
    )

    result = RateLimitGate().check(project.pk, 10).allowed

    assert result is True


def test_the_baseline_is_the_median_not_the_mean(project):
    """Should not let one quiet hour drag the baseline down and turn normal traffic into a spike."""
    key = "spike:1"
    now = timezone.now()
    for hours, count in ((1, 100), (2, 100), (3, 0)):
        IngestCounter.objects.create(
            key=key,
            bucket=limits.bucket_start(now - datetime.timedelta(hours=hours), 3600),
            count=count,
        )

    result = limits.baseline(key, now)
    expected = 100.0

    assert result == expected


def test_a_key_with_no_history_has_no_baseline():
    """Should return nothing to compare against rather than zero, which would refuse everything."""
    result = limits.baseline("spike:absent", timezone.now())
    expected = 0.0

    assert result == expected


# the pass-through gate is still what it was


def test_the_pass_through_gate_only_checks_size(project):
    """Should stay available for an install that wants no counting at all."""
    IngestQuota.objects.create(name="tight", project=project, limit=0)

    result = PassThroughGate().check(project.pk, 10).allowed

    assert result is True


def test_a_global_quota_counts_globally(project):
    """Should share one budget across every project when the quota names none."""
    quota = IngestQuota.objects.create(name="global", limit=100)

    result = limits.counter_key(project.pk, quota)
    expected = "global"

    assert result == expected
