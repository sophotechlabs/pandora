import datetime

import pytest
from django.utils import timezone

from pandora.events import relevance
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def on(settings):
    settings.PANDORA_RETENTION_BY_RELEVANCE = True
    settings.PANDORA_RELEVANCE_BUDGET = 100
    settings.PANDORA_RELEVANCE_HALF_LIFE_DAYS = 7
    return settings


# the scoring


def test_a_fresh_issue_keeps_its_whole_budget(on):
    """Should not thin what is happening right now."""
    result = relevance.keep_for(seen=1000, age_days=0)
    expected = 100

    assert result == expected


def test_the_budget_halves_at_the_half_life(on):
    """Should be the whole shape of the model, in one number an operator sets."""
    result = relevance.keep_for(seen=1000, age_days=7)
    expected = 50

    assert result == expected


def test_a_rare_issue_is_never_thinned_below_what_it_has(on):
    """Should be the point — the one occurrence of the rare bug survives."""
    result = relevance.keep_for(seen=1, age_days=365)
    expected = 1

    assert result == expected


def test_at_least_one_copy_always_survives(on):
    """Should never leave an issue with no evidence at all behind it."""
    result = relevance.keep_for(seen=5000, age_days=3650)
    expected = 1

    assert result == expected


def test_a_noisy_old_issue_is_thinned_hard(on):
    """Should be the other half — the thousandth copy of the flood goes."""
    result = relevance.keep_for(seen=5000, age_days=28)

    assert 1 < result < 20


# the switch


def test_it_is_off_by_default(settings):
    """Should not change what an install deletes until it is measured."""
    settings.PANDORA_RETENTION_BY_RELEVANCE = False

    assert relevance.enabled() is False


def test_a_verdict_is_produced_per_issue(on, issue):
    """Should say what it would drop before anything drops it."""
    issue_models.Issue.objects.filter(pk=issue.pk).update(
        event_count=1000, last_seen=NOW - datetime.timedelta(days=14)
    )

    verdict = relevance.verdicts(NOW)[0]

    assert verdict.issue_id == issue.pk
    assert verdict.dropping > 0


def test_an_issue_inside_its_budget_drops_nothing(on, issue):
    """Should leave alone what does not need thinning."""
    issue_models.Issue.objects.filter(pk=issue.pk).update(event_count=3, last_seen=NOW)

    result = relevance.verdicts(NOW)[0].dropping
    expected = 0

    assert result == expected
