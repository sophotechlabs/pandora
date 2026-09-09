import datetime

import pytest

from pandora.issues import aggregates, models

MOMENT = datetime.datetime(2026, 9, 7, 9, 12, tzinfo=datetime.UTC)

pytestmark = pytest.mark.django_db


def count(issue, key, moment=MOMENT):
    aggregates.count_user(issue, key, moment)


def test_the_first_sighting_of_a_person_counts_one(issue):
    """Should move the number the stream shows off zero."""
    count(issue, "id:42")

    issue.refresh_from_db()
    assert issue.user_count == 1
    assert models.IssueUser.objects.filter(issue=issue).count() == 1


def test_the_same_person_seen_twice_counts_once(issue):
    """Should answer how many people, not how many events."""
    count(issue, "id:42")
    count(issue, "id:42")

    issue.refresh_from_db()
    assert issue.user_count == 1


def test_two_people_count_two(issue):
    """Should separate identities the payload declared as different."""
    count(issue, "id:42")
    count(issue, "email:someone@example.test")

    issue.refresh_from_db()
    assert issue.user_count == 2


def test_an_empty_identity_counts_nobody(issue):
    """Should not count an event that carried no person at all."""
    count(issue, "")
    count(issue, "   ")

    issue.refresh_from_db()
    assert issue.user_count == 0
    assert not models.IssueUser.objects.filter(issue=issue).exists()


def test_counting_stops_at_the_cap_and_says_so(issue, settings):
    """Should stop writing rows for an issue that reached everybody."""
    settings.PANDORA_USER_COUNT_CAP = 3
    for index in range(6):
        count(issue, f"id:{index}")

    issue.refresh_from_db()
    assert issue.user_count == 3
    assert issue.users_capped is True
    assert models.IssueUser.objects.filter(issue=issue).count() == 3


def test_a_cap_of_zero_never_caps(issue, settings):
    """Should read zero as no limit, the way the retention settings do."""
    settings.PANDORA_USER_COUNT_CAP = 0
    for index in range(5):
        count(issue, f"id:{index}")

    issue.refresh_from_db()
    assert issue.user_count == 5
    assert issue.users_capped is False


def test_an_occurrence_counts_the_person_its_user_tag_names(issue):
    """Should run off the same tag the breakdown and the search read."""
    aggregates.count_occurrence(issue, MOMENT, {"user": "id:42", "url": "/checkout"})

    issue.refresh_from_db()
    assert issue.user_count == 1


def test_an_occurrence_without_a_user_tag_counts_nobody(issue):
    """Should leave a server-side error with no person attached at zero."""
    aggregates.count_occurrence(issue, MOMENT, {"url": "/checkout"})

    issue.refresh_from_db()
    assert issue.user_count == 0


def test_a_long_identity_is_cut_to_the_column_width(issue):
    """Should store a truncated key rather than raise on a huge one."""
    count(issue, "id:" + "a" * 500)

    row = models.IssueUser.objects.get(issue=issue)
    assert len(row.key) == aggregates.KEY_MAX


def test_a_rebuild_recounts_the_people_its_samples_name(issue):
    """Should leave a regrouped issue counting only the events it kept."""
    models.IssueUser.objects.create(issue=issue, key="id:gone")
    issue.user_count = 1
    issue.save(update_fields=["user_count"])

    aggregates.rebuild_from(
        issue,
        [
            (MOMENT, {"user": "id:42"}),
            (MOMENT, {"user": "id:42"}),
            (MOMENT, {"user": "id:7"}),
        ],
    )

    issue.refresh_from_db()
    result = (issue.user_count, sorted(row.key for row in issue.affected_users.all()))
    expected = (2, ["id:42", "id:7"])

    assert result == expected


def test_a_rebuild_with_no_people_clears_the_count(issue):
    """Should not leave a stale number behind when the events carried nobody."""
    models.IssueUser.objects.create(issue=issue, key="id:gone")
    issue.user_count = 1
    issue.save(update_fields=["user_count"])

    aggregates.rebuild_from(issue, [(MOMENT, {"namespace": "payments"})])

    issue.refresh_from_db()
    assert issue.user_count == 0


def test_a_rebuild_past_the_cap_marks_the_issue(issue, settings):
    """Should keep the rebuilt count bounded the same way live counting is."""
    settings.PANDORA_USER_COUNT_CAP = 2

    aggregates.rebuild_from(
        issue, [(MOMENT, {"user": f"id:{index}"}) for index in range(5)]
    )

    issue.refresh_from_db()
    result = (issue.user_count, issue.users_capped)
    expected = (2, True)

    assert result == expected
