import datetime
import io

import pytest
from django import test
from django.core import management
from django.core.management import base
from django.utils import timezone

from pandora.am import reconcile
from pandora.core import models as core_models
from pandora.issues import models as issue_models
from tests.am import fake_am

pytestmark = pytest.mark.django_db

COMMAND = "pandora.am.management.commands.reconcile"


@pytest.fixture
def moment():
    return timezone.now().replace(microsecond=0)


@pytest.fixture
def configured(alertmanager, settings):
    settings.PANDORA_AM_URL = alertmanager.url
    settings.PANDORA_AM_CA_BUNDLE = ""
    return alertmanager


def run_command(*args):
    out = io.StringIO()
    err = io.StringIO()
    management.call_command("reconcile", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


# argument contract


def test_the_command_takes_the_flags_the_deployment_passes(token, configured):
    """Should accept the loop, scope and metrics flags Phase 5 will set."""
    parser = management.load_command_class("pandora.am", "reconcile").create_parser(
        "manage.py", "reconcile"
    )

    result = sorted(
        action.dest
        for action in parser._actions
        if action.dest in ("loop", "project", "environment", "metrics_port")
    )
    expected = ["environment", "loop", "metrics_port", "project"]

    assert result == expected


# one pass


def test_one_pass_reports_what_it_saw(token, configured, moment):
    """Should print a single line an operator can read in the pod log."""
    configured.alerts = [fake_am.alert("3c1f6a2b9d4e5087", {"alertname": "TargetDown"})]

    out, err = run_command()

    result = out
    expected = "reconcile: 1 alerts, 0 open episodes, 1 opened, 0 closed, 0 missing\n"

    assert result == expected
    assert err == ""


def test_one_pass_applies_the_catch_up(token, configured):
    """Should leave the database corrected, not only report on it."""
    configured.alerts = [fake_am.alert("3c1f6a2b9d4e5087", {"alertname": "TargetDown"})]

    run_command()

    result = issue_models.Episode.objects.count()
    expected = 1

    assert result == expected


def test_a_refused_read_is_reported_on_stderr(token, configured):
    """Should keep the exit code clean — the loop retries, it does not crash."""
    configured.fail_next(404, times=40)

    out, err = run_command()

    assert out == ""
    assert "could not read alertmanager" in err


# scope failures


def test_a_missing_token_stops_the_command(project, configured):
    """Should name the missing binding instead of reconciling nothing forever."""
    with pytest.raises(base.CommandError) as error:
        run_command()

    assert "no active Alertmanager ingest token" in str(error.value)


def test_an_ambiguous_scope_stops_the_command(token, configured):
    """Should make the operator name the cluster this Deployment reconciles."""
    core_models.IngestToken.objects.create(
        project=token.project,
        name="p-mk2 alertmanager",
        token="second-ingest-token",
        source=core_models.TokenSource.AM,
        environment="p-mk2",
    )

    with pytest.raises(base.CommandError) as error:
        run_command()

    assert "narrow with --project and --environment" in str(error.value)


def test_the_scope_flags_are_passed_through(token, configured):
    """Should reconcile the cluster the flags name."""
    core_models.IngestToken.objects.create(
        project=token.project,
        name="p-mk2 alertmanager",
        token="second-ingest-token",
        source=core_models.TokenSource.AM,
        environment="p-mk2",
    )

    out, _ = run_command("--project", "infrastructure", "--environment", "p-mk2")

    result = out
    expected = "reconcile: 0 alerts, 0 open episodes, 0 opened, 0 closed, 0 missing\n"

    assert result == expected


@test.override_settings(PANDORA_AM_URL="")
def test_an_unconfigured_alertmanager_stops_the_command(token):
    """Should fail at startup, where the deployment can see it."""
    with pytest.raises(base.CommandError) as error:
        run_command()

    assert "PANDORA_AM_URL" in str(error.value)


# loop


def test_the_loop_keeps_going_until_it_is_interrupted(token, configured, mocker):
    """Should run pass after pass, sleeping the interval between them."""
    sleep = mocker.patch(
        f"{COMMAND}.time.sleep", side_effect=[None, None, KeyboardInterrupt]
    )

    out, _ = run_command("--loop", "60")

    result = (out.count("reconcile: 0 alerts"), sleep.call_args_list)
    expected = (3, [mocker.call(60), mocker.call(60), mocker.call(60)])

    assert result == expected
    assert out.endswith("reconcile: stopped\n")


def test_a_single_pass_never_sleeps(token, configured, mocker):
    """Should exit straight away without --loop, so a CronJob shape also works."""
    sleep = mocker.patch(f"{COMMAND}.time.sleep")

    run_command()

    result = sleep.call_count
    expected = 0

    assert result == expected


def test_the_loop_survives_an_alertmanager_outage(token, configured, mocker):
    """Should report the failure and try again, not exit the Deployment."""
    configured.fail_next(404, times=40)
    mocker.patch(f"{COMMAND}.time.sleep", side_effect=[None, KeyboardInterrupt])

    out, err = run_command("--loop", "60")

    result = err.count("could not read alertmanager")
    expected = 2

    assert result == expected
    assert out.endswith("reconcile: stopped\n")


# metrics


def test_the_metrics_server_stays_off_by_default(token, configured, mocker):
    """Should bind no port unless the deployment asks for one."""
    serve = mocker.patch(f"{COMMAND}.prometheus_client.start_http_server")

    run_command()

    result = serve.call_count
    expected = 0

    assert result == expected


def test_the_metrics_port_is_served_when_asked(token, configured, mocker):
    """Should expose the watchdog gauge from the reconcile pod, which has no web port."""
    serve = mocker.patch(f"{COMMAND}.prometheus_client.start_http_server")

    out, _ = run_command("--metrics-port", "9109")

    result = serve.call_args_list
    expected = [mocker.call(9109)]

    assert result == expected
    assert "reconcile: metrics on :9109" in out


def test_the_watchdog_gauge_is_named_as_the_alert_rule_expects(token, configured):
    """Should keep the metric name the Phase 5 PrometheusRule is written against."""
    reconcile.WATCHDOG_SEEN.set(0)
    configured.alerts = [
        fake_am.alert(
            "0f0e0d0c0b0a0908",
            {"alertname": "Watchdog"},
            starts_at=(timezone.now() - datetime.timedelta(days=1)).isoformat(),
        )
    ]

    run_command()

    result = reconcile.WATCHDOG_SEEN._value.get() > 0

    assert result is True
