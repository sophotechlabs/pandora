import datetime
import io

import pytest
from django.core import management
from django.utils import timezone

from pandora.releases import models

pytestmark = pytest.mark.django_db


def test_the_sweep_times_out_only_old_started_deploys(project):
    now = timezone.now()
    release = models.Release.objects.create(project=project, version="1.2.3")
    old = models.Deploy.objects.create(
        release=release,
        started_at=now - datetime.timedelta(hours=2),
    )
    recent = models.Deploy.objects.create(release=release, started_at=now)
    finished = models.Deploy.objects.create(
        release=release,
        state=models.DeployState.SUCCEEDED,
        started_at=now - datetime.timedelta(hours=2),
        finished_at=now,
    )

    out = io.StringIO()
    management.call_command("rollouts", stdout=out)

    old.refresh_from_db()
    recent.refresh_from_db()
    finished.refresh_from_db()
    assert old.state == models.DeployState.TIMED_OUT
    assert recent.state == models.DeployState.STARTED
    assert finished.state == models.DeployState.SUCCEEDED
    assert "rollouts: 1 timed out" in out.getvalue()


def test_the_sweep_is_safe_when_nothing_is_waiting():
    out = io.StringIO()

    management.call_command("rollouts", stdout=out)

    assert out.getvalue() == "rollouts: 0 timed out\n"
