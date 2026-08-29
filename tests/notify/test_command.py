import io

import pytest
from django.core import management
from django.core.management.base import CommandError

from pandora.notify.models import Delivery

pytestmark = pytest.mark.django_db


class FakeResponse:
    status_code = 200


def run(*args):
    out = io.StringIO()
    management.call_command("deliver", *args, stdout=out)
    return out.getvalue()


def test_a_single_pass_sends_what_is_queued(make_issue, make_destination, mocker):
    """Should be runnable from a cron job as well as a loop."""
    mocker.patch("pandora.notify.senders.requests.post", return_value=FakeResponse())
    Delivery.objects.create(
        destination=make_destination(),
        issue=make_issue(),
        event="issue.new",
        payload={"event": "issue.new", "issue": {"title": "boom", "url": "/"}},
    )

    output = run()

    result = ("1 sent" in output, Delivery.objects.get().state)
    expected = (True, "sent")

    assert result == expected


def test_an_empty_queue_reports_nothing_sent():
    """Should be safe to run on a schedule when there is nothing to do."""
    output = run()

    result = "0 sent, 0 retried, 0 failed" in output

    assert result is True


def test_a_limit_below_one_is_an_error():
    """Should not accept a batch size that would never make progress."""
    with pytest.raises(CommandError, match="--limit must be at least 1"):
        run("--limit", "0")


def test_the_metrics_port_is_served_when_asked(mocker):
    """Should let a loop deployment be scraped, the way reconcile already is."""
    server = mocker.patch(
        "pandora.notify.management.commands.deliver.start_http_server"
    )

    run("--metrics-port", "9110")

    server.assert_called_once_with(9110)


def test_the_loop_sleeps_between_passes(mocker):
    """Should be deployable as a long-running process rather than a cron job."""
    sleep = mocker.patch(
        "pandora.notify.management.commands.deliver.time.sleep",
        side_effect=[None, KeyboardInterrupt],
    )

    with pytest.raises(KeyboardInterrupt):
        run("--loop", "5")

    result = sleep.call_args_list[0].args
    expected = (5,)

    assert result == expected
