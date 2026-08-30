import datetime

import pytest
from django.utils import timezone

from pandora.events.store import get_store
from tests.events import support

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def filled(project):
    store = get_store()

    def build(issue_id, count):
        events = [
            support.make_event(
                index,
                NOW,
                project_id=project.pk,
                issue_id=issue_id,
                episode_id=None,
                id=f"01J{issue_id:011d}{index:012d}",
                timestamp=NOW + datetime.timedelta(seconds=index),
            )
            for index in range(count)
        ]
        store.insert(events)
        return store

    return build


def remaining(store, project, issue_id):
    return list(store.fetch(project.pk, issue_id=issue_id, limit=100))


def test_thinning_keeps_the_newest(filled, project):
    """Should drop the old copies of a flood and keep what is worth reading."""
    store = filled(10, count=6)

    store.thin(10, keep=2)

    result = len(remaining(store, project, 10))
    expected = 2

    assert result == expected


def test_the_survivors_are_the_most_recent(filled, project):
    """Should be newest-first — the id is a ULID, so largest is latest."""
    store = filled(10, count=5)

    store.thin(10, keep=1)

    result = remaining(store, project, 10)[0].id
    expected = f"01J{10:011d}{4:012d}"

    assert result == expected


def test_thinning_leaves_other_issues_alone(filled, project):
    """Should be per issue, which is the whole point of the relevance model."""
    store = filled(10, count=4)
    filled(11, count=4)

    store.thin(10, keep=1)

    result = len(remaining(store, project, 11))
    expected = 4

    assert result == expected


def test_keeping_more_than_exists_drops_nothing(filled, project):
    """Should not be surprised by a budget larger than the issue."""
    store = filled(10, count=2)

    result = store.thin(10, keep=10)
    expected = 0

    assert result == expected


def test_a_negative_budget_drops_nothing(filled, project):
    """Should refuse a nonsense budget rather than empty the table."""
    store = filled(10, count=3)

    result = store.thin(10, keep=-1)
    expected = 0

    assert result == expected
