import pytest
from django.contrib.auth import models as auth_models
from django.utils import timezone

from pandora.issues import comments, models

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def dev(db):
    return auth_models.User.objects.create_user(
        username="dev", email="dev@example.test", is_staff=True
    )


def test_a_comment_lands_in_the_activity_trail(issue, dev):
    """Should read in order beside the state changes it is about."""
    comments.add(issue, dev, "looks like a bad deploy", NOW)

    activity = models.IssueActivity.objects.get(kind=models.ActivityKind.COMMENTED)
    result = (activity.actor, activity.data["body"])
    expected = ("dev", "looks like a bad deploy")

    assert result == expected


def test_commenting_starts_watching(issue, dev):
    """Should not make a person ask twice to hear the answer to their question."""
    comments.add(issue, dev, "any idea?", NOW)

    assert comments.watching(issue, dev) is True


def test_the_reason_says_why_the_subscription_exists(issue, dev):
    """Should let a reader see it was inferred, not chosen."""
    comments.add(issue, dev, "any idea?", NOW)

    subscription = models.Subscription.objects.get()
    assert subscription.reason == models.SubscriptionReason.COMMENTED


def test_an_empty_comment_is_refused(issue, dev):
    """Should not write a row for an accidental submit."""
    with pytest.raises(comments.CommentError):
        comments.add(issue, dev, "   ", NOW)


def test_a_long_comment_is_cut_to_the_limit(issue, dev):
    """Should bound the column rather than raise on a paste."""
    comments.add(issue, dev, "x" * 9000, NOW)

    activity = models.IssueActivity.objects.get(kind=models.ActivityKind.COMMENTED)
    assert len(activity.data["body"]) == comments.BODY_MAX


def test_watching_can_be_chosen_outright(issue, dev):
    """Should let somebody follow an issue they have not touched."""
    comments.watch(issue, dev, NOW)

    assert comments.watching(issue, dev) is True


def test_watching_can_be_stopped(issue, dev):
    """Should be reversible, which is the whole point of a subscription row."""
    comments.watch(issue, dev, NOW)

    stopped = comments.unwatch(issue, dev)

    result = (stopped, comments.watching(issue, dev))
    expected = (True, False)

    assert result == expected


def test_stopping_twice_reports_nothing_changed(issue, dev):
    """Should tell the caller the second click did nothing."""
    comments.watch(issue, dev, NOW)
    comments.unwatch(issue, dev)

    assert comments.unwatch(issue, dev) is False


def test_watching_again_after_stopping_works(issue, dev):
    """Should reuse the row rather than fail on the unique constraint."""
    comments.watch(issue, dev, NOW)
    comments.unwatch(issue, dev)

    comments.watch(issue, dev, NOW)

    assert comments.watching(issue, dev) is True


def test_an_anonymous_reader_watches_nothing(issue):
    """Should not query the table for a request with no account."""
    from django.contrib.auth.models import AnonymousUser

    assert comments.watching(issue, AnonymousUser()) is False


def test_the_addresses_are_the_watchers_with_an_email(issue, dev):
    """Should be what a comment notification is actually delivered to."""
    silent = auth_models.User.objects.create_user(username="silent", email="")
    comments.watch(issue, dev, NOW)
    comments.watch(issue, silent, NOW)

    assert comments.addresses(issue) == ["dev@example.test"]


def test_an_unwatched_person_is_not_an_address(issue, dev):
    """Should stop mailing somebody who asked to stop."""
    comments.watch(issue, dev, NOW)
    comments.unwatch(issue, dev)

    assert comments.addresses(issue) == []


def test_a_comment_queues_a_notification(issue, dev, settings):
    """Should tell the team, which is why a note is worth writing."""
    from pandora.notify import models as notify_models

    settings.PANDORA_COMMENT_HOOKS = "pandora.notify.hooks.on_comment"
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.COMMENT],
        min_level=models.Level.DEBUG,
    )

    comments.add(issue, dev, "restarting the pod", NOW)

    delivery = notify_models.Delivery.objects.get()
    result = (delivery.event, delivery.payload["body"], delivery.payload["subscribers"])
    expected = (notify_models.COMMENT, "restarting the pod", ["dev@example.test"])

    assert result == expected


def test_the_comment_shows_in_the_issue_detail(issue, dev):
    """Should be rendered where a reader is already looking."""
    from pandora.issues import detail

    comments.add(issue, dev, "looks like a bad deploy", NOW)

    built = detail.build(issue)
    notes = [row.note for row in built.activities]

    assert "looks like a bad deploy" in notes
