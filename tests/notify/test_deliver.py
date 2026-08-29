import datetime

import pytest
from django.core import mail
from django.utils import timezone

from pandora.notify import deliver, senders
from pandora.notify.models import Delivery, DeliveryState, DestinationKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def queued(make_issue, make_destination):
    def build(destination=None, count=1, **overrides):
        destination = destination or make_destination()
        issue = make_issue()
        rows = [
            Delivery.objects.create(
                destination=destination,
                issue=issue,
                event="issue.new",
                payload={
                    "event": "issue.new",
                    "issue": {
                        "title": f"boom {index}",
                        "project": "infrastructure",
                        "environment": "p-mk1",
                        "url": "https://pandora.test/issues/1/",
                    },
                },
                **overrides,
            )
            for index in range(count)
        ]
        return destination, rows

    return build


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


# sending


def test_a_webhook_is_posted_with_the_payload(queued, mocker):
    """Should hand the receiver exactly what was queued, so a replay is meaningful."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    queued()

    report = deliver.run_once()

    result = (report.sent, post.call_count)
    expected = (1, 1)

    assert result == expected


def test_a_sent_delivery_is_marked(queued, mocker):
    """Should never send the same notification twice."""
    mocker.patch("pandora.notify.senders.requests.post", return_value=FakeResponse())
    queued()

    deliver.run_once()
    deliver.run_once()

    result = (Delivery.objects.get().state, Delivery.objects.get().sent_at is not None)
    expected = (DeliveryState.SENT, True)

    assert result == expected


def test_a_webhook_is_signed_when_a_secret_is_set(queued, mocker):
    """Should let a receiver prove the call came from this Pandora."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination, _ = queued()
    destination.secret = "shared-secret"
    destination.save(update_fields=["secret"])

    deliver.run_once()
    headers = post.call_args.kwargs["headers"]

    result = (
        senders.SIGNATURE_HEADER in headers,
        headers[senders.SIGNATURE_HEADER]
        == senders.sign("shared-secret", post.call_args.kwargs["data"]),
    )
    expected = (True, True)

    assert result == expected


def test_an_unsigned_webhook_carries_no_signature(queued, mocker):
    """Should not send an empty signature that a receiver might trust."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    queued()

    deliver.run_once()

    result = senders.SIGNATURE_HEADER in post.call_args.kwargs["headers"]

    assert result is False


def test_email_goes_to_every_recipient(queued, make_destination):
    """Should reach a list, because on-call is rarely one address."""
    destination = make_destination(
        kind=DestinationKind.EMAIL, target="a@example.test, b@example.test"
    )
    queued(destination=destination)

    deliver.run_once()

    result = sorted(mail.outbox[0].to)
    expected = ["a@example.test", "b@example.test"]

    assert result == expected


def test_an_email_destination_with_no_recipients_fails(queued, make_destination):
    """Should record the misconfiguration rather than silently sending nothing."""
    destination = make_destination(kind=DestinationKind.EMAIL, target="  ")
    queued(destination=destination)

    report = deliver.run_once()

    result = (report.sent, report.retried, len(mail.outbox))
    expected = (0, 1, 0)

    assert result == expected


def test_discord_uses_its_own_field(queued, make_destination, mocker):
    """Should speak each chat service's own body, or the message never appears."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination = make_destination(kind=DestinationKind.DISCORD)
    queued(destination=destination)

    deliver.run_once()

    result = "content" in post.call_args.kwargs["data"].decode()

    assert result is True


def test_slack_uses_text(queued, make_destination, mocker):
    """Should use the field Slack's incoming webhooks read."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination = make_destination(kind=DestinationKind.SLACK)
    queued(destination=destination)

    deliver.run_once()

    result = '"text"' in post.call_args.kwargs["data"].decode()

    assert result is True


def test_an_unknown_kind_is_reported(queued, make_destination, mocker):
    """Should not crash the worker on a row someone edited by hand."""
    destination = make_destination()
    destination.kind = "carrier-pigeon"
    destination.save(update_fields=["kind"])
    queued(destination=destination)

    report = deliver.run_once()

    result = report.retried
    expected = 1

    assert result == expected


# failure and retry


def test_a_refused_webhook_is_retried_later(queued, mocker):
    """Should back off rather than hammering a receiver that is down."""
    mocker.patch("pandora.notify.senders.requests.post", return_value=FakeResponse(500))
    queued()

    report = deliver.run_once()
    row = Delivery.objects.get()

    result = (report.retried, row.state, row.attempts, row.send_after is not None)
    expected = (1, DeliveryState.PENDING, 1, True)

    assert result == expected


def test_a_delivery_waiting_for_its_backoff_is_skipped(queued, mocker):
    """Should honour the wait it just set rather than retrying immediately."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse(500)
    )
    queued()
    deliver.run_once()
    post.reset_mock()

    deliver.run_once()

    result = post.call_count
    expected = 0

    assert result == expected


def test_a_delivery_gives_up_after_enough_attempts(queued, mocker):
    """Should stop retrying a receiver that is never coming back."""
    mocker.patch("pandora.notify.senders.requests.post", return_value=FakeResponse(500))
    _, rows = queued()
    rows[0].attempts = deliver.MAX_ATTEMPTS - 1
    rows[0].save(update_fields=["attempts"])

    report = deliver.run_once()

    result = (report.failed, Delivery.objects.get().state)
    expected = (1, DeliveryState.FAILED)

    assert result == expected


def test_a_network_error_is_recorded_not_raised(queued, mocker):
    """Should keep the worker alive when a host stops resolving."""
    import requests

    mocker.patch(
        "pandora.notify.senders.requests.post",
        side_effect=requests.ConnectionError("no route"),
    )
    queued()

    report = deliver.run_once()

    result = (report.retried, "no route" in Delivery.objects.get().error)
    expected = (1, True)

    assert result == expected


# digests


def test_a_digest_holds_deliveries_until_its_window_passes(
    queued, make_destination, mocker
):
    """Should collect a storm into one message rather than sending fifty."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination = make_destination(digest_seconds=300)
    queued(destination=destination, count=3)

    report = deliver.run_once()

    result = (report.sent, post.call_count)
    expected = (0, 0)

    assert result == expected


def test_a_digest_sends_one_message_for_many_issues(queued, make_destination, mocker):
    """Should be one notification, not one per row, once the window has passed."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination = make_destination(digest_seconds=60)
    queued(destination=destination, count=3)
    Delivery.objects.update(created_at=timezone.now() - datetime.timedelta(minutes=5))

    report = deliver.run_once()
    body = post.call_args.kwargs["data"].decode()

    result = (report.sent, post.call_count, '"issue.digest"' in body)
    expected = (3, 1, True)

    assert result == expected


def test_a_pass_batches_per_destination_even_without_a_digest(
    queued, make_destination, mocker
):
    """Should not make two calls to one channel in one pass — digest_seconds decides how long to wait for more, not whether to batch what is already there."""
    post = mocker.patch(
        "pandora.notify.senders.requests.post", return_value=FakeResponse()
    )
    destination = make_destination(digest_seconds=0)
    queued(destination=destination, count=2)

    report = deliver.run_once()
    body = post.call_args.kwargs["data"].decode()

    result = (report.sent, post.call_count, '"issue.digest"' in body)
    expected = (2, 1, True)

    assert result == expected


# housekeeping


def test_sent_deliveries_are_pruned(queued, mocker):
    """Should not keep a row per notification forever."""
    mocker.patch("pandora.notify.senders.requests.post", return_value=FakeResponse())
    queued()
    deliver.run_once()
    Delivery.objects.update(sent_at=timezone.now() - datetime.timedelta(days=90))

    removed = deliver.prune(timezone.now() - datetime.timedelta(days=30))

    result = (removed, Delivery.objects.count())
    expected = (1, 0)

    assert result == expected


def test_a_pending_delivery_is_never_pruned(queued):
    """Should never drop something that has not been sent yet."""
    queued()

    removed = deliver.prune(timezone.now())

    result = (removed, Delivery.objects.count())
    expected = (0, 1)

    assert result == expected


def test_a_digest_email_says_how_many(queued, make_destination):
    """Should not put one issue's title on a message about five."""
    destination = make_destination(
        kind=DestinationKind.EMAIL, target="ops@example.test", digest_seconds=0
    )
    queued(destination=destination, count=3)

    deliver.run_once()

    result = mail.outbox[0].subject
    expected = "Pandora: 3 issues need attention"

    assert result == expected


def test_mail_that_nothing_accepted_is_retried(queued, make_destination, mocker):
    """Should treat a silent SMTP failure as a failure rather than a delivery."""
    mocker.patch("pandora.notify.senders.send_mail", return_value=0)
    destination = make_destination(
        kind=DestinationKind.EMAIL, target="ops@example.test"
    )
    queued(destination=destination)

    report = deliver.run_once()

    result = (report.sent, report.retried)
    expected = (0, 1)

    assert result == expected


def test_a_destination_names_itself_readably(make_destination):
    """Should read as what it is in the admin listings."""
    result = str(make_destination(name="ops", kind=DestinationKind.SLACK))
    expected = "ops (slack)"

    assert result == expected


def test_a_delivery_names_its_event_and_state(queued):
    """Should be identifiable in the delivery log without opening the row."""
    _, rows = queued()

    result = str(rows[0]).startswith("issue.new to ")

    assert result is True
