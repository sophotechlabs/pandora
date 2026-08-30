import datetime

import pytest
from django.utils import timezone

from pandora.issues import models as issue_models
from pandora.issues import ranking

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def counted(issue):
    def build(hours_ago, count):
        return issue_models.HourlyStat.objects.create(
            issue=issue,
            hour=(NOW - datetime.timedelta(hours=hours_ago)).replace(
                minute=0, second=0, microsecond=0
            ),
            count=count,
        )

    return build


def test_an_issue_with_no_history_has_no_rate_change(issue):
    """Should be one, meaning unchanged, rather than a division by nothing."""
    result = ranking.rate_change(issue, NOW)
    expected = 0.0

    assert result == expected


def test_a_new_burst_reads_as_a_rise(issue, counted):
    """Should be the number the join and the ranking both ask for."""
    counted(1, 24)

    result = ranking.rate_change(issue, NOW)

    assert result > 1


def test_a_steady_issue_reads_as_unchanged(issue, counted):
    """Should not call a constant rate a spike, which is the bug that hides real ones."""
    for hours in range(1, 24 * 8):
        counted(hours, 1)

    result = ranking.rate_change(issue, NOW)

    assert 0.8 < result < 1.3


def test_the_breadth_keys_come_from_the_setting(settings):
    """Should let an operator name the labels that mean 'somewhere' on their cluster."""
    settings.PANDORA_BREADTH_KEYS = "pod, node ,"

    result = ranking.breadth_keys()
    expected = ("pod", "node")

    assert result == expected


def test_no_breadth_keys_is_an_empty_tuple(settings):
    """Should not turn an empty setting into a key called ''."""
    settings.PANDORA_BREADTH_KEYS = ""

    result = ranking.breadth_keys()
    expected = ()

    assert result == expected
